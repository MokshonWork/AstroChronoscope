"""IRSA SIA2 client: HTTP access, VOTable parsing and coordinate validation.

Verified against the live service: SIA2 is served from
https://irsa.ipac.caltech.edu/SIA and requires POS=CIRCLE ra dec radius.
VOTable columns carry both ``name`` and ``ID``; Astropy names them after the ID
(``col_0``...), so the mapping is rebuilt explicitly.
"""

from __future__ import annotations

import io
import logging
import math
import re
from typing import Any

import requests
from astropy.io.votable import parse as parse_votable
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    IrsaHttpError,
    IrsaQueryError,
    IrsaResponseError,
    IrsaTimeoutError,
    IrsaUnavailableError,
)
from app.core.validation import validate_coordinates, validate_radius

log = logging.getLogger(__name__)

#: Columns copied out of every SIA2 row.
ROW_FIELDS: tuple[str, ...] = (
    "obs_id",
    "obs_collection",
    "obs_publisher_did",
    "access_url",
    "access_format",
    "access_estsize",
    "calib_level",
    "dataproduct_type",
    "dataproduct_subtype",
    "energy_bandpassname",
    "facility_name",
    "instrument_name",
    "target_name",
    "s_ra",
    "s_dec",
    "s_xel1",
    "s_xel2",
    "s_pixel_scale",
    "s_fov",
    "t_min",
    "t_max",
    "t_exptime",
    "em_min",
    "em_max",
    "pol_states",
    "s_region",
)

_VOTABLE_TYPES = ("application/x-votable+xml", "text/xml", "application/xml", "text/plain")


class IrsaClient:
    """Reusable, retrying HTTP client for IRSA SIA2."""

    def __init__(self, settings: Settings | None = None, session: requests.Session | None = None) -> None:
        self.settings = settings or get_settings()
        self._session = session

    # ------------------------------------------------------------------ HTTP
    @property
    def session(self) -> requests.Session:
        if self._session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": self.settings.user_agent, "Accept": "application/x-votable+xml"})
            retry = Retry(
                total=2,
                connect=2,
                read=0,
                backoff_factor=1.0,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=("GET",),
            )
            adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            self._session = session
        return self._session

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    # --------------------------------------------------------------- queries
    def search(
        self,
        ra: float,
        dec: float,
        radius: float,
        collection: str | None = None,
        start_mjd: float | None = None,
        end_mjd: float | None = None,
    ) -> list[dict[str, Any]]:
        """Run a cone search and return parsed rows (empty list is valid)."""
        ra, dec = validate_coordinates(ra, dec)
        validate_radius(radius, self.settings.max_search_radius_deg)

        params: dict[str, str] = {
            "POS": f"CIRCLE {ra:.8f} {dec:.8f} {radius:.8f}",
            "RESPONSEFORMAT": "votable",
        }
        if collection:
            params["COLLECTION"] = collection
        if start_mjd is not None or end_mjd is not None:
            lo = start_mjd if start_mjd is not None else 0.0
            hi = end_mjd if end_mjd is not None else 100000.0
            params["TIME"] = f"{lo:.6f} {hi:.6f}"

        rows = self._query(params)
        return [row for row in rows if self._in_time_window(row, start_mjd, end_mjd)]

    def find_by_publisher_did(self, publisher_did: str, collection: str | None = None) -> dict[str, Any] | None:
        """Resolve a single SIA row by its unique ``obs_publisher_did``."""
        params: dict[str, str] = {"ID": publisher_did, "RESPONSEFORMAT": "votable"}
        hint = collection or _collection_from_did(publisher_did)
        if hint:
            params["COLLECTION"] = hint
        for row in self._query(params):
            if row.get("obs_publisher_did") == publisher_did:
                return row
        return None

    def _query(self, params: dict[str, str]) -> list[dict[str, Any]]:
        url = self.settings.sia_endpoint
        try:
            response = self.session.get(
                url,
                params=params,
                timeout=(self.settings.connect_timeout, self.settings.request_timeout),
            )
        except requests.Timeout as exc:
            raise IrsaTimeoutError("IRSA SIA request timed out", details={"endpoint": url}) from exc
        except requests.ConnectionError as exc:
            raise IrsaUnavailableError("Could not connect to IRSA SIA", details={"endpoint": url}) from exc
        except requests.RequestException as exc:
            raise IrsaUnavailableError("IRSA SIA request failed", details={"endpoint": url, "error": str(exc)}) from exc

        if response.status_code >= 400:
            raise IrsaHttpError(
                f"IRSA SIA returned HTTP {response.status_code}",
                details={"status_code": response.status_code, "endpoint": url},
            )

        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type and content_type not in _VOTABLE_TYPES:
            raise IrsaResponseError(
                "IRSA SIA returned a non-VOTable response",
                details={"content_type": content_type, "endpoint": url},
            )

        return self._parse_votable(response.content, params)

    def _parse_votable(self, payload: bytes, params: dict[str, str]) -> list[dict[str, Any]]:
        try:
            votable = parse_votable(io.BytesIO(payload), verify="ignore")
        except Exception as exc:  # astropy raises several unrelated parser errors
            raise IrsaResponseError("Could not parse the IRSA VOTable response") from exc

        status, message = "OK", None
        for resource in votable.resources:
            for info in getattr(resource, "infos", []):
                if info.name == "QUERY_STATUS":
                    status = (info.value or "").upper()
                    message = info.content

        if status not in ("OK", "OVERFLOW"):
            raise IrsaQueryError(
                "IRSA rejected the SIA query",
                details={"query_status": status, "irsa_message": message, "params": _redact(params)},
            )

        tables = list(votable.iter_tables())
        if not tables:
            if status == "OK":
                return []
            raise IrsaResponseError("IRSA response contained no VOTable table")

        table = tables[0].to_table()
        name_map = {
            field.ID: field.name
            for field in tables[0].fields
            if field.ID and field.name and field.ID in table.colnames and field.ID != field.name
        }
        if name_map:
            table.rename_columns(list(name_map), [name_map[c] for c in name_map])

        rows: list[dict[str, Any]] = []
        for index in range(len(table)):
            row = {}
            for field in ROW_FIELDS:
                row[field] = _cell(table, field, index)
            rows.append(row)
        log.info("irsa_sia_query", extra={"rows": len(rows), "params": _redact(params)})
        return rows

    @staticmethod
    def _in_time_window(row: dict[str, Any], start_mjd: float | None, end_mjd: float | None) -> bool:
        t_min, t_max = _as_float(row.get("t_min")), _as_float(row.get("t_max"))
        if start_mjd is not None and t_max is not None and t_max < start_mjd:
            return False
        if end_mjd is not None and t_min is not None and t_min > end_mjd:
            return False
        return True


def _cell(table: Any, field: str, index: int) -> Any:
    if field not in table.colnames:
        return None
    value = table[field][index]
    try:
        import numpy as np

        if np.ma.is_masked(value):
            return None
        if isinstance(value, np.generic):
            value = value.item()
    except Exception:  # pragma: no cover - defensive
        pass
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _as_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _collection_from_did(publisher_did: str) -> str | None:
    match = re.match(r"^ivo://[^/]+/([^?]+)\?", publisher_did)
    return match.group(1) if match else None


def _redact(params: dict[str, str]) -> dict[str, str]:
    """Query parameters only; nothing secret is ever sent, but keep logs compact."""
    return dict(params)

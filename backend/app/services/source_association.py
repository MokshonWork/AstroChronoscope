"""Angular cross-epoch association with an explicit ambiguity audit trail.

The matcher is a transparent nearest-neighbour association on the sphere. It
never silently merges two detections that are both plausible counterparts: the
ambiguity is recorded and the source is flagged for review. A duplicate detection
inside an epoch that has already been matched is excluded with a reason instead of
forcing a second light-curve point at the same time.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

from astropy.coordinates import SkyCoord
from astropy import units as u

from app.schemas.lightcurve import (
    ASSOCIATION_ALGORITHM_VERSION,
    AssociatedSource,
    AssociationConfig,
    AssociationSummary,
    EpochMeasurements,
    ExcludedDetection,
    MatchedDetection,
)
from app.schemas.photometry import FluxMeasurement


@dataclass
class _State:
    source_id: str
    x: float
    y: float
    z: float
    count: int = 0
    epochs: set[str] = field(default_factory=set)
    detections: list[MatchedDetection] = field(default_factory=list)
    bands: set[str] = field(default_factory=set)
    flags: set[str] = field(default_factory=set)
    ambiguity_count: int = 0

    @property
    def ra(self) -> float:
        return math.degrees(math.atan2(self.y, self.x)) % 360.0

    @property
    def dec(self) -> float:
        return math.degrees(math.atan2(self.z, math.hypot(self.x, self.y)))

    def add(self, measurement: FluxMeasurement, epoch_key: str, separation: float | None, reference: float | None) -> None:
        unit = _unit_vector(float(measurement.ra_deg), float(measurement.dec_deg))
        self.x += unit[0]
        self.y += unit[1]
        self.z += unit[2]
        self.count += 1
        self.epochs.add(epoch_key)
        if measurement.band:
            self.bands.add(measurement.band)
        self.detections.append(
            MatchedDetection(
                measurement=measurement,
                epoch_key=epoch_key,
                separation_arcsec=_round(separation),
                reference_separation_arcsec=_round(reference),
                flags=[],
            )
        )


def associate_epochs(
    epochs: list[EpochMeasurements],
    config: AssociationConfig,
) -> tuple[list[AssociatedSource], AssociationSummary]:
    ordered = sorted(
        epochs,
        key=lambda item: (item.time_mjd if item.time_mjd is not None else math.inf, item.epoch_key),
    )
    states: list[_State] = []
    excluded: list[ExcludedDetection] = []
    total_detections = 0
    ambiguous = 0
    duplicates = 0
    missing = 0

    for epoch in ordered:
        used_sources: set[str] = set()
        measurements = sorted(
            epoch.measurements,
            key=lambda item: -(item.snr if item.snr is not None else -math.inf),
        )
        for measurement in measurements:
            total_detections += 1
            if measurement.ra_deg is None or measurement.dec_deg is None:
                missing += 1
                excluded.append(
                    ExcludedDetection(
                        detection_id=measurement.detection_id,
                        observation_id=measurement.observation_id,
                        epoch_key=epoch.epoch_key,
                        reason="missing_position",
                    )
                )
                continue

            candidates = _candidates(measurement, states, config)
            if not candidates:
                state = _new_state(measurement, epoch.epoch_key)
                states.append(state)
                used_sources.add(state.source_id)
                continue

            available = [item for item in candidates if item[1].source_id not in used_sources]
            if not available:
                _distance, state = candidates[0]
                if _already_seen(measurement.detection_id, state, epoch.epoch_key):
                    duplicates += 1
                    state.flags.add("duplicate_detection_in_epoch")
                    excluded.append(
                        ExcludedDetection(
                            detection_id=measurement.detection_id,
                            observation_id=measurement.observation_id,
                            epoch_key=epoch.epoch_key,
                            reason="duplicate_same_epoch",
                        )
                    )
                    continue
                # A distinct detection close to an already-matched source in the
                # same epoch is kept as its own source, with the crowding flagged.
                new_state = _new_state(measurement, epoch.epoch_key)
                new_state.flags.add("same_epoch_close_pair")
                states.append(new_state)
                used_sources.add(new_state.source_id)
                ambiguous += 1
                continue

            distance, state = available[0]
            reference = _separation(measurement.ra_deg, measurement.dec_deg, state.ra, state.dec)
            if _has_ambiguity(measurement, states, config, state.source_id):
                ambiguous += 1
                state.ambiguity_count += 1
                state.flags.add("ambiguous_match")
            used_sources.add(state.source_id)
            state.add(measurement, epoch.epoch_key, distance, reference)

    sources = [_finalize(state, config) for state in states]
    sources.sort(
        key=lambda item: (-item.epoch_count, -item.detection_count, item.source_id)
    )
    summary = AssociationSummary(
        match_radius_arcsec=config.match_radius_arcsec,
        input_epochs=len(epochs),
        input_detections=total_detections,
        source_count=len(sources),
        ambiguous_matches=ambiguous,
        duplicates_dropped=duplicates,
        missing_position_dropped=missing,
        excluded=excluded,
    )
    return sources[: config.max_sources], summary


def _candidates(
    measurement: FluxMeasurement, states: list[_State], config: AssociationConfig
) -> list[tuple[float, _State]]:
    found: list[tuple[float, _State]] = []
    for state in states:
        separation = _separation(float(measurement.ra_deg), float(measurement.dec_deg), state.ra, state.dec)
        if separation <= config.match_radius_arcsec:
            found.append((separation, state))
    found.sort(key=lambda item: item[0])
    return found


def _has_ambiguity(
    measurement: FluxMeasurement,
    states: list[_State],
    config: AssociationConfig,
    chosen_id: str,
) -> bool:
    limit = config.match_radius_arcsec + config.ambiguity_margin_arcsec
    count = 0
    for state in states:
        if state.source_id == chosen_id:
            continue
        separation = _separation(float(measurement.ra_deg), float(measurement.dec_deg), state.ra, state.dec)
        if separation <= limit:
            count += 1
    return count > 0


def _already_seen(detection_id: str, state: _State, epoch_key: str) -> bool:
    return any(
        item.measurement.detection_id == detection_id and item.epoch_key == epoch_key
        for item in state.detections
    )


def _new_state(measurement: FluxMeasurement, epoch_key: str) -> _State:
    source_id = hashlib.sha256(
        f"{ASSOCIATION_ALGORITHM_VERSION}|{epoch_key}|{measurement.detection_id}".encode("utf-8")
    ).hexdigest()[:32]
    state = _State(source_id=source_id, x=0.0, y=0.0, z=0.0)
    state.add(measurement, epoch_key, 0.0, 0.0)
    return state


def _finalize(state: _State, config: AssociationConfig) -> AssociatedSource:
    if state.count < 2:
        state.flags.add("single_epoch")
    if state.ambiguity_count:
        state.flags.add("ambiguous_match")
    scatters = [
        _separation(
            float(item.measurement.ra_deg), float(item.measurement.dec_deg), state.ra, state.dec
        )
        for item in state.detections
    ]
    scatter = (
        math.sqrt(sum(value ** 2 for value in scatters) / len(scatters)) if scatters else None
    )
    if scatter is not None and scatter > config.match_radius_arcsec:
        state.flags.add("large_position_scatter")
    if any(item.measurement.band is None for item in state.detections):
        state.flags.add("missing_band")
    return AssociatedSource(
        source_id=state.source_id,
        reference_ra_deg=_round(state.ra, 8),
        reference_dec_deg=_round(state.dec, 8),
        detection_count=state.count,
        epoch_count=len(state.epochs),
        bands=sorted(state.bands),
        detections=sorted(
            state.detections,
            key=lambda item: (item.measurement.time_mjd if item.measurement.time_mjd is not None else math.inf, item.measurement.detection_id),
        ),
        position_scatter_arcsec=_round(scatter),
        ambiguity_count=state.ambiguity_count,
        flags=sorted(state.flags),
    )


def _unit_vector(ra: float, dec: float) -> tuple[float, float, float]:
    ra_r, dec_r = math.radians(ra), math.radians(dec)
    return (
        math.cos(dec_r) * math.cos(ra_r),
        math.cos(dec_r) * math.sin(ra_r),
        math.sin(dec_r),
    )


def _separation(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    first = SkyCoord(ra1 * u.deg, dec1 * u.deg, frame="icrs")
    second = SkyCoord(ra2 * u.deg, dec2 * u.deg, frame="icrs")
    return float(first.separation(second).to_value(u.arcsec))


def _round(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


__all__ = ["associate_epochs"]

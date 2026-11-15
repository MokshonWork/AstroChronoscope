"""Typed application errors mapped to consistent JSON API responses."""

from __future__ import annotations

from typing import Any


class PlanetXError(Exception):
    """Base class for every expected failure."""

    code = "INTERNAL_ERROR"
    status_code = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class InvalidCoordinateError(PlanetXError):
    code = "INVALID_COORDINATES"
    status_code = 400


class InvalidRadiusError(PlanetXError):
    code = "INVALID_RADIUS"
    status_code = 400


class InvalidDateError(PlanetXError):
    code = "INVALID_DATE_RANGE"
    status_code = 400


class InvalidBandFilterError(PlanetXError):
    code = "INVALID_BAND_FILTER"
    status_code = 400


class InvalidCutoutSizeError(PlanetXError):
    code = "INVALID_CUTOUT_SIZE"
    status_code = 400


class IrsaTimeoutError(PlanetXError):
    code = "IRSA_TIMEOUT"
    status_code = 504


class IrsaUnavailableError(PlanetXError):
    code = "IRSA_UNAVAILABLE"
    status_code = 503


class IrsaHttpError(PlanetXError):
    code = "IRSA_HTTP_ERROR"
    status_code = 502


class IrsaQueryError(PlanetXError):
    code = "IRSA_QUERY_ERROR"
    status_code = 502


class IrsaResponseError(PlanetXError):
    code = "IRSA_INVALID_RESPONSE"
    status_code = 502


class GaiaTimeoutError(PlanetXError):
    """Gaia Archive TAP request timed out."""

    code = "GAIA_TIMEOUT"
    status_code = 504


class GaiaUnavailableError(PlanetXError):
    """Gaia Archive is unreachable or temporarily unavailable."""

    code = "GAIA_UNAVAILABLE"
    status_code = 503


class GaiaHttpError(PlanetXError):
    """Gaia Archive returned an HTTP error."""

    code = "GAIA_HTTP_ERROR"
    status_code = 502


class GaiaQueryError(PlanetXError):
    """Gaia Archive rejected the ADQL query."""

    code = "GAIA_QUERY_ERROR"
    status_code = 502


class GaiaResponseError(PlanetXError):
    """Gaia Archive returned an unparseable response."""

    code = "GAIA_INVALID_RESPONSE"
    status_code = 502


class GaiaSourceNotFoundError(PlanetXError):
    """No Gaia DR3 record exists for that source identifier."""

    code = "GAIA_SOURCE_NOT_FOUND"
    status_code = 404


class InvalidGaiaSourceIdError(PlanetXError):
    """The requested source identifier is not a valid Gaia source_id."""

    code = "INVALID_GAIA_SOURCE_ID"
    status_code = 400


class GaiaRateLimitError(PlanetXError):
    """Gaia Archive asked the client to back off (429)."""

    code = "GAIA_RATE_LIMITED"
    status_code = 503


class ObservationNotFoundError(PlanetXError):
    code = "OBSERVATION_NOT_FOUND"
    status_code = 404


class MissingAccessUrlError(PlanetXError):
    code = "MISSING_ACCESS_URL"
    status_code = 422


class UntrustedUrlError(PlanetXError):
    code = "UNTRUSTED_ACCESS_URL"
    status_code = 422


class UnsupportedProductError(PlanetXError):
    code = "UNSUPPORTED_PRODUCT"
    status_code = 422


class CutoutUnavailableError(PlanetXError):
    code = "IRSA_CUTOUT_UNAVAILABLE"
    status_code = 503


class InvalidFitsFileError(PlanetXError):
    code = "INVALID_FITS_FILE"
    status_code = 502


class MissingImageExtensionError(PlanetXError):
    code = "MISSING_IMAGE_EXTENSION"
    status_code = 422


class PreviewGenerationError(PlanetXError):
    code = "PREVIEW_GENERATION_FAILED"
    status_code = 500


class StoredFileNotFoundError(PlanetXError):
    code = "FILE_NOT_FOUND"
    status_code = 404


class ComparisonNotFoundError(PlanetXError):
    code = "COMPARISON_NOT_FOUND"
    status_code = 404


class ImageProcessingLimitError(PlanetXError):
    code = "IMAGE_PROCESSING_LIMIT"
    status_code = 422


class ProcessingBusyError(PlanetXError):
    code = "PROCESSING_BUSY"
    status_code = 503


class ReprojectionError(PlanetXError):
    code = "REPROJECTION_FAILED"
    status_code = 422

"""Shared API schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class Target(BaseModel):
    ra: float
    dec: float


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    success: Literal[False] = False
    error: ErrorDetail


class StrictModel(BaseModel):
    """Request models reject unknown fields so typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


__all__ = ["ErrorDetail", "ErrorResponse", "StrictModel", "Target"]

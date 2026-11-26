"""Stable MCP result envelope. Remote text is data, never agent instructions."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Result(BaseModel):
    source: str = "blackboard"
    fetched_at: str = Field(default_factory=now_iso)
    cached: bool = False
    complete: bool = True
    state: str = "ok"
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)

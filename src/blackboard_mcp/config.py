"""Explicit TOML configuration shared by login, MCP reads and monitoring."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .errors import BlackboardError

API_PREFIX = "/learn/api/public/v1"


@dataclass(frozen=True)
class Settings:
    base_url: str = "https://elearning.shanghaitech.edu.cn:8443"
    profile_dir: Path = Path(".auth/browser-profile")
    download_dir: Path = Path("downloads")
    max_download_bytes: int = 50 * 1024 * 1024
    max_total_download_bytes: int = 200 * 1024 * 1024
    max_download_files: int = 20
    timeout_seconds: float = 15
    max_retries: int = 2
    min_interval_seconds: float = 0.3
    max_pages: int = 20
    max_items: int = 2000
    course_id: str | None = None
    parent_content_id: str | None = None
    current_term_id: str | None = None
    timezone: str = "Asia/Shanghai"
    max_depth: int = 12
    max_nodes: int = 1000
    max_courses: int = 20
    max_requests: int = 300
    tool_timeout_seconds: float = 180

    def __post_init__(self):
        try:
            parsed = urlsplit(self.base_url)
            valid = (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and parsed.path in ("", "/")
                and not parsed.query
                and not parsed.fragment
                and 1 <= (parsed.port if parsed.port is not None else 443) <= 65535
                and not any(c.isspace() or c == "\\" for c in self.base_url)
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("base_url must be an HTTPS origin without credentials or a path")
        bounds = {
            "max_download_bytes": (self.max_download_bytes, 1, 1024 * 1024 * 1024),
            "max_total_download_bytes": (self.max_total_download_bytes, 1, 2 * 1024**3),
            "max_download_files": (self.max_download_files, 1, 100),
            "timeout_seconds": (self.timeout_seconds, 1, 60),
            "max_retries": (self.max_retries, 0, 3),
            "min_interval_seconds": (self.min_interval_seconds, 0, 10),
            "max_pages": (self.max_pages, 1, 100),
            "max_items": (self.max_items, 1, 10000),
            "max_depth": (self.max_depth, 1, 30),
            "max_nodes": (self.max_nodes, 1, 5000),
            "max_courses": (self.max_courses, 1, 100),
            "max_requests": (self.max_requests, 1, 1000),
            "tool_timeout_seconds": (self.tool_timeout_seconds, 5, 600),
        }
        for name, (value, lower, upper) in bounds.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
            if not lower <= value <= upper:
                raise ValueError(f"{name} is outside the supported range")
        for name in (
            "max_download_bytes",
            "max_total_download_bytes",
            "max_download_files",
            "max_retries",
            "max_pages",
            "max_items",
            "max_depth",
            "max_nodes",
            "max_courses",
            "max_requests",
        ):
            if not isinstance(getattr(self, name), int):
                raise ValueError(f"{name} must be an integer")
        if self.current_term_id is not None and (
            not isinstance(self.current_term_id, str)
            or not re.fullmatch(r"_[0-9]+_[0-9]+", self.current_term_id)
        ):
            raise ValueError("current_term_id must be an internal ID")
        ZoneInfo(self.timezone)
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))


def load_settings(path: Path) -> Settings:
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
        sections = {
            "blackboard": {"base_url", "current_term_id", "timezone"},
            "auth": {"profile_dir"},
            "downloads": {
                "download_dir",
                "max_download_bytes",
                "max_total_download_bytes",
                "max_download_files",
            },
            "network": {
                "timeout_seconds",
                "max_retries",
                "min_interval_seconds",
                "max_pages",
                "max_items",
            },
            "probe": {"course_id", "parent_content_id"},
            "limits": {
                "max_depth",
                "max_nodes",
                "max_courses",
                "max_requests",
                "tool_timeout_seconds",
            },
        }
        if set(data) - set(sections):
            raise ValueError("Unknown configuration section")
        values = {}
        for section, allowed in sections.items():
            part = data.get(section, {})
            if not isinstance(part, dict) or set(part) - allowed:
                raise ValueError("Unknown configuration key")
            values.update(part)
        profile = Path(values.get("profile_dir", ".auth/browser-profile")).expanduser()
        if not profile.is_absolute():
            profile = path.resolve().parent / profile
        values["profile_dir"] = profile.resolve()
        download_dir = Path(values.get("download_dir", "downloads")).expanduser()
        if not download_dir.is_absolute():
            download_dir = path.resolve().parent / download_dir
        values["download_dir"] = download_dir.resolve()
        for name in ("course_id", "parent_content_id"):
            if name in values and not isinstance(values[name], str):
                raise ValueError("Probe IDs must be strings")
        return Settings(**values)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise BlackboardError(
            "configuration_error", "Cannot read configuration; check config.example.toml."
        ) from exc

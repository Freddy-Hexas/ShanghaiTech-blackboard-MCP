"""Async GET-only client with bounded pagination and fail-closed URL validation."""

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from .config import API_PREFIX, Settings
from .errors import BlackboardError


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


class Transport(Protocol):
    async def get(self, url: str, *, timeout_ms: float) -> Response: ...


ID = r"_[0-9]+_[0-9]+"
READ_PATH = re.compile(
    rf"^(?:/system/version|/users/me/courses|/courses/{ID}"
    rf"(?:/contents(?:/{ID}/(?:children|attachments))?|/gradebook/columns)?)$"
)


def validate_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(ID, value):
        raise BlackboardError("invalid_id", "Expected an internal Blackboard ID.")
    return value


class BlackboardClient:
    def __init__(self, transport: Transport, settings: Settings):
        self.transport = transport
        self.settings = settings
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self.request_count = 0
        self.authenticated = False
        self.authentication_failed = False

    def resolve_url(self, endpoint: str) -> str:
        if not isinstance(endpoint, str) or not endpoint:
            raise BlackboardError("unsafe_url", "Invalid API path.")
        # Reject encoded separators, controls, and dot segments before URL normalization.
        decoded = endpoint
        for _ in range(3):
            decoded = unquote(decoded)
        try:
            if (
                any(ord(c) < 33 or ord(c) == 127 or c == "\\" for c in decoded)
                or decoded.startswith("//")
                or any(p in (".", "..") for p in urlsplit(decoded).path.split("/"))
            ):
                raise ValueError("invalid path")
            parsed = urlsplit(endpoint)
            base = urlsplit(self.settings.base_url)
            if parsed.scheme or parsed.netloc:
                if (
                    parsed.scheme != "https"
                    or parsed.hostname != base.hostname
                    or (parsed.port or 443) != (base.port or 443)
                    or parsed.username is not None
                    or parsed.password is not None
                ):
                    raise ValueError("origin")
            elif not endpoint.startswith("/"):
                raise ValueError("relative path")
            if parsed.fragment or "%" in parsed.path:
                raise ValueError("fragment or encoded path")
            path = parsed.path
            if path.startswith(API_PREFIX + "/"):
                path = path[len(API_PREFIX) :]
            if not READ_PATH.fullmatch(path):
                raise ValueError("unsupported path")
            params = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
            if len(params) != len({key for key, _ in params}):
                raise ValueError("duplicate parameters")
            for key, value in params:
                if key not in ("offset", "limit") or not value.isascii() or not value.isdigit():
                    raise ValueError("unsupported query")
                if int(value) > 1000000 or (key == "limit" and not 1 <= int(value) <= 200):
                    raise ValueError("query bounds")
            query = "&".join(f"{key}={int(value)}" for key, value in sorted(params))
            return urlunsplit((base.scheme, base.netloc, API_PREFIX + path, query, ""))
        except (TypeError, ValueError) as exc:
            raise BlackboardError("unsafe_url", "URL outside the allowed API scope.") from exc

    async def get(self, endpoint: str) -> dict:
        url = self.resolve_url(endpoint)
        async with self._lock:
            for attempt in range(self.settings.max_retries + 1):
                await self.pace_request()
                try:
                    response = await self.transport.get(
                        url, timeout_ms=self.settings.timeout_seconds * 1000
                    )
                except BlackboardError as exc:
                    if exc.code == "network_error" and attempt < self.settings.max_retries:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise

                status = response.status
                if status in (401, 403) or 300 <= status < 400:
                    code = "access_denied" if status == 403 else "login_required"
                    if code == "login_required":
                        self.authentication_failed = True
                    raise BlackboardError(code, "Authentication or access check failed.", status)
                if status == 429 or 500 <= status < 600:
                    code = "rate_limited" if status == 429 else "server_error"
                    if attempt < self.settings.max_retries:
                        delay = self._retry_delay(response.headers.get("retry-after"), attempt)
                        if delay is not None:
                            await asyncio.sleep(delay)
                            continue
                    raise BlackboardError(code, "Request stopped after bounded retry.", status)
                if not 200 <= status < 300:
                    code = {400: "bad_request", 404: "not_found"}.get(status, "http_error")
                    raise BlackboardError(code, "Blackboard rejected the read request.", status)
                content_type = response.headers.get("content-type", "").split(";")[0].strip()
                if content_type.lower() not in ("application/json", "application/problem+json"):
                    code = (
                        "login_required" if "html" in content_type.lower() else "invalid_response"
                    )
                    if code == "login_required":
                        self.authentication_failed = True
                    raise BlackboardError(code, "Expected JSON; received a different page.", status)
                if len(response.body) > 5 * 1024 * 1024:
                    raise BlackboardError("response_too_large", "JSON response exceeds 5 MiB.")
                try:
                    data = json.loads(response.body)
                except (ValueError, UnicodeError) as exc:
                    raise BlackboardError("invalid_response", "Malformed JSON response.") from exc
                if not isinstance(data, dict):
                    raise BlackboardError("invalid_response", "Expected a JSON object.")
                return data
        raise AssertionError("Unreachable retry state")

    async def pace_request(self):
        """Shared request budget, including HTML reads and download redirect hops."""
        if self.request_count >= self.settings.max_requests:
            raise BlackboardError("request_limit", "Query request budget reached.")
        self.request_count += 1
        delay = self.settings.min_interval_seconds - (time.monotonic() - self._last_request)
        if delay > 0:
            await asyncio.sleep(delay)
        self._last_request = time.monotonic()

    def read_status(self, status: int):
        if status == 200:
            return
        code = {
            400: "bad_request",
            401: "login_required",
            403: "access_denied",
            404: "not_found",
            429: "rate_limited",
        }.get(status, "server_error" if status >= 500 else "unexpected_response")
        if 300 <= status < 400:
            code = "login_required"
        if code == "login_required":
            self.authentication_failed = True
        raise BlackboardError(code, "Blackboard rejected the read request.", status)

    async def content_page(self, course_id: str, parent_id: str) -> bytes:
        validate_id(course_id)
        validate_id(parent_id)
        url = (
            self.settings.base_url
            + "/webapps/blackboard/content/listContent.jsp"
            + f"?course_id={course_id}&content_id={parent_id}"
        )
        async with self._lock:
            await self.pace_request()
            response = await self.transport.get(
                url, timeout_ms=self.settings.timeout_seconds * 1000
            )
            self.read_status(response.status)
            if "text/html" not in response.headers.get("content-type", "").lower():
                raise BlackboardError("invalid_response", "Expected a Classic content page.")
            if len(response.body) > 5 * 1024 * 1024:
                raise BlackboardError("response_too_large", "Content page exceeds 5 MiB.")
            return response.body

    @staticmethod
    def _retry_delay(value: str | None, attempt: int) -> float | None:
        if value is None:
            return 0.5 * (2**attempt)
        try:
            seconds = float(value)
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    return None
                seconds = (date - datetime.now(UTC)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return None
        # Never clamp a long Retry-After and retry earlier than the server requested.
        return max(0.0, seconds) if seconds <= 30 else None

    async def get_all(self, endpoint: str) -> list[dict]:
        current = self.resolve_url(endpoint)
        collection_path = urlsplit(current).path
        visited: set[str] = set()
        items: list[dict] = []
        for _ in range(self.settings.max_pages):
            if current in visited:
                raise BlackboardError("pagination_loop", "Repeated pagination URL.")
            visited.add(current)
            data = await self.get(current)
            results = data.get("results")
            paging = data.get("paging", {})
            if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
                raise BlackboardError(
                    "invalid_response", "Expected results to be a list of objects."
                )
            if not isinstance(paging, dict):
                raise BlackboardError("invalid_response", "Expected a paging object.")
            items.extend(results)
            if len(items) > self.settings.max_items:
                raise BlackboardError(
                    "pagination_limit", "Item limit reached; result is incomplete."
                )
            next_page = paging.get("nextPage")
            if next_page is None:
                return items
            if not isinstance(next_page, str) or not next_page:
                raise BlackboardError("invalid_response", "Expected a nonempty nextPage URL.")
            current = self.resolve_url(next_page)
            if urlsplit(current).path != collection_path:
                raise BlackboardError("unsafe_url", "Pagination changed the collection path.")
        raise BlackboardError("pagination_limit", "Page limit reached; result is incomplete.")

    async def memberships(self) -> list[dict]:
        memberships = await self.get_all("/users/me/courses")
        for row in memberships:
            validate_id(row.get("courseId"))
        self.authenticated = True
        return memberships

    async def require_membership(self, course_id: str) -> None:
        validate_id(course_id)
        if course_id not in {row["courseId"] for row in await self.memberships()}:
            raise BlackboardError("access_denied", "Course is not in current user's memberships.")

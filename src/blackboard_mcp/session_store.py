"""Private session persistence without changing cookie scope or expiration."""

import ctypes
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

from .config import API_PREFIX, Settings
from .errors import BlackboardError

STATE_FILE = "blackboard-session.dat"
WINDOWS_HEADER = b"BBMCP-SESSION-1-DPAPI\n"
POSIX_HEADER = b"BBMCP-SESSION-1-POSIX\n"


def _dpapi(data: bytes, origin: str, *, decrypt: bool = False) -> bytes:
    """Windows user-bound encryption, with the configured origin as entropy."""
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    source_buffer = ctypes.create_string_buffer(data)
    entropy_bytes = ("blackboard-mcp:" + origin).encode("utf-8")
    entropy_buffer = ctypes.create_string_buffer(entropy_bytes)
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy = Blob(len(entropy_bytes), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN; deliberately not CRYPTPROTECT_LOCAL_MACHINE.
    if not function(
        ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output)
    ):
        raise OSError("Windows session protection failed")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def _validate_cookies(cookies: list, settings: Settings) -> list[dict]:
    host = urlsplit(settings.base_url).hostname
    now = time.time()
    retained = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            raise ValueError("Invalid cookie structure")
        if not all(isinstance(cookie.get(key), str) for key in ("name", "value", "domain", "path")):
            raise ValueError("Missing cookie fields")
        domain = cookie["domain"].lower()
        if not (host == domain.lstrip(".") or (domain.startswith(".") and host.endswith(domain))):
            raise ValueError("Cookie is outside the configured host")
        expires = cookie.get("expires", -1)
        if type(expires) not in (float, int) or not (expires == -1 or expires > 0):
            raise ValueError("Invalid cookie expiry")
        if expires != -1 and expires <= now:
            continue
        retained.append(cookie)
    return retained


async def save_session(context, settings: Settings) -> int:
    """Call only after the current-user API has confirmed authentication."""
    cookies = await context.cookies(settings.base_url + API_PREFIX + "/users/me/courses")
    return save_cookies(cookies, settings)


def save_cookies(cookies: list[dict], settings: Settings) -> int:
    """Persist validated API cookies after authentication, without logging their values."""
    temporary = None
    try:
        cookies = _validate_cookies(cookies, settings)
        if not cookies:
            raise ValueError("No reusable API cookies")
        payload = json.dumps(
            {
                "schema": 1,
                "origin": settings.base_url,
                "saved_at": datetime.now(UTC).isoformat(),
                "cookies": cookies,
            }
        ).encode("utf-8")
        protected = (
            WINDOWS_HEADER + _dpapi(payload, settings.base_url)
            if os.name == "nt"
            else POSIX_HEADER + payload
        )
        # The parent is already a protected, dedicated profile directory.
        descriptor, name = tempfile.mkstemp(prefix=".session-", dir=settings.profile_dir)
        temporary = settings.profile_dir / name
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(protected)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(settings.profile_dir / STATE_FILE)
        return len(cookies)
    except (OSError, ValueError, TypeError) as exc:
        raise BlackboardError(
            "session_error", "Cannot save the private Blackboard session."
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_session(settings: Settings) -> list[dict] | None:
    path = settings.profile_dir / STATE_FILE
    try:
        if not path.exists():
            return None
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Session file is too large")
        protected = path.read_bytes()
        header = WINDOWS_HEADER if os.name == "nt" else POSIX_HEADER
        if not protected.startswith(header):
            raise ValueError("Unexpected session file format")
        payload = protected[len(header) :]
        if os.name == "nt":
            payload = _dpapi(payload, settings.base_url, decrypt=True)
        state = json.loads(payload)
        if (
            not isinstance(state, dict)
            or state.get("schema") != 1
            or state.get("origin") != settings.base_url
            or not isinstance(state.get("cookies"), list)
        ):
            raise ValueError("Invalid session envelope")
        return _validate_cookies(state["cookies"], settings)
    except (OSError, ValueError, TypeError) as exc:
        raise BlackboardError(
            "session_error", "Cannot restore the private session; run login again."
        ) from exc


def forget_session(settings: Settings) -> None:
    try:
        (settings.profile_dir / STATE_FILE).unlink(missing_ok=True)
    except OSError as exc:
        raise BlackboardError("session_error", "Cannot remove the expired session cache.") from exc

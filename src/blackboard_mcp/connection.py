"""Short-lived API sessions; no persistent Chromium process in the MCP server."""

from contextlib import asynccontextmanager

from filelock import FileLock, Timeout

from .auth import PROFILE_MARKER
from .client import BlackboardClient
from .config import Settings
from .errors import BlackboardError
from .http_transport import HttpTransport
from .session_store import forget_session, load_session, save_cookies


@asynccontextmanager
async def api_client(settings: Settings):
    if not (settings.profile_dir / PROFILE_MARKER).is_file():
        raise BlackboardError("login_required", "Run blackboard-probe login in your terminal.")
    try:
        # A different process may be logging in or refreshing this account's cookie jar.
        with FileLock(settings.profile_dir / ".session.lock", timeout=0):
            cookies = load_session(settings)
            if not cookies:
                raise BlackboardError(
                    "login_required", "Run blackboard-probe login in your terminal."
                )
            async with HttpTransport(cookies) as transport:
                client = BlackboardClient(transport, settings)
                try:
                    yield client
                finally:
                    if client.authentication_failed:
                        forget_session(settings)
                    elif client.authenticated:
                        save_cookies(transport.cookies(), settings)
    except Timeout as exc:
        raise BlackboardError(
            "session_busy", "Another query or login is using this account; retry later."
        ) from exc
    except OSError as exc:
        raise BlackboardError(
            "session_error", "Cannot access the private session directory."
        ) from exc

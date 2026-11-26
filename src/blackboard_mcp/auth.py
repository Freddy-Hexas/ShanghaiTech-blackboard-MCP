"""A dedicated Playwright profile; never imports cookies from other browsers."""

import csv
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from filelock import FileLock, Timeout
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from .client import Response
from .config import Settings
from .errors import BlackboardError
from .session_store import load_session

PROFILE_MARKER = ".blackboard-probe-profile"


def prepare_profile(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        marker = path / PROFILE_MARKER
        if not marker.is_file() and any(path.iterdir()):
            raise BlackboardError(
                "profile_error", "Use an empty, dedicated browser profile directory."
            )
        if os.name == "nt":
            # Restrict only this dedicated directory. Children inherit the current user's ACE.
            account = subprocess.run(
                ["whoami", "/user", "/fo", "csv", "/nh"],
                check=True,
                capture_output=True,
                text=True,
            )
            sid = next(csv.reader(account.stdout.strip().splitlines()))[-1]
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F", "/q"],
                check=True,
                capture_output=True,
            )
        else:
            path.chmod(0o700)
        marker.touch(mode=0o600, exist_ok=True)
    except (OSError, subprocess.SubprocessError, IndexError, StopIteration) as exc:
        raise BlackboardError(
            "profile_error", "Cannot protect the dedicated profile directory."
        ) from exc


class PlaywrightTransport:
    def __init__(self, request):
        self.request = request

    async def get(self, url: str, *, timeout_ms: float) -> Response:
        response = None
        try:
            response = await self.request.get(
                url,
                headers={"Accept": "application/json"},
                timeout=timeout_ms,
                max_redirects=0,
                fail_on_status_code=False,
            )
            return Response(response.status, response.headers, await response.body())
        except PlaywrightError as exc:
            # Playwright exceptions can contain URLs, headers and browser logs.
            raise BlackboardError(
                "network_error", "Request failed; check network and TLS."
            ) from exc
        finally:
            if response is not None:
                await response.dispose()


@asynccontextmanager
async def browser_session(settings: Settings, *, interactive: bool = False):
    if not interactive and not (settings.profile_dir / PROFILE_MARKER).is_file():
        raise BlackboardError(
            "login_required", "Initialize the dedicated profile with login first."
        )
    try:
        prepare_profile(settings.profile_dir)
        lock = FileLock(settings.profile_dir / ".session.lock", timeout=0)
        lock.acquire()
    except Timeout as exc:
        raise BlackboardError(
            "session_busy", "Another query or login is using this account."
        ) from exc
    except OSError as exc:
        raise BlackboardError("profile_error", "Cannot lock the dedicated profile.") from exc
    try:
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(settings.profile_dir),
                headless=not interactive,
                ignore_https_errors=False,
                accept_downloads=False,
                timeout=30000,
            )
            try:
                if not interactive:
                    cookies = load_session(settings)
                    if cookies:
                        await context.add_cookies(cookies)
                yield context
            finally:
                await context.close()
    except PlaywrightError as exc:
        raise BlackboardError(
            "browser_error",
            "Cannot use profile; install Chromium and close processes using this profile.",
        ) from exc
    except OSError as exc:
        raise BlackboardError(
            "profile_error", "Cannot access the dedicated profile directory."
        ) from exc
    finally:
        lock.release()


@asynccontextmanager
async def anonymous_transport():
    """A negative control with no browser and no stored login state."""
    try:
        async with async_playwright() as playwright:
            request = await playwright.request.new_context(ignore_https_errors=False)
            try:
                yield PlaywrightTransport(request)
            finally:
                await request.dispose()
    except PlaywrightError as exc:
        raise BlackboardError(
            "browser_error", "Cannot initialize Playwright request context."
        ) from exc

"""Human-invoked commands. This module is deliberately not an MCP server."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .auth import PlaywrightTransport, anonymous_transport, browser_session
from .client import BlackboardClient
from .config import load_settings
from .errors import BlackboardError
from .probe import run_probe
from .session_store import forget_session, save_session


async def run(args) -> dict:
    settings = load_settings(args.config)
    if args.command == "probe" and args.anonymous:
        async with anonymous_transport() as transport:
            report = await run_probe(BlackboardClient(transport, settings), settings)
            report["transport"] = "anonymous_api_request_context"
            return report
    if args.command == "login" and not sys.stdin.isatty():
        raise BlackboardError(
            "interactive_required", "Run login directly in an interactive terminal."
        )
    async with browser_session(settings, interactive=args.command == "login") as context:
        client = BlackboardClient(PlaywrightTransport(context.request), settings)
        if args.command == "login":
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(settings.base_url, wait_until="domcontentloaded", timeout=30000)
            print(
                "Complete Blackboard login in the dedicated browser, then press Enter here.",
                file=sys.stderr,
            )
            await asyncio.to_thread(input)
        if args.command in ("login", "status"):
            try:
                memberships = await client.memberships()
            except BlackboardError as exc:
                if exc.code == "login_required":
                    forget_session(settings)
                raise
            await save_session(context, settings)
            return {
                "state": "authenticated",
                "membership_count": len(memberships),
                "transport": "browser_context_request",
                "session_saved": True,
            }
        report = await run_probe(client, settings)
        report["transport"] = "browser_context_request"
        if report["state"] == "authenticated":
            await save_session(context, settings)
            report["session_saved"] = True
        elif report["state"] == "login_required":
            forget_session(settings)
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.local.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("login", help="Open a dedicated browser for manual sign-in")
    commands.add_parser("status", help="Check existing profile with the current-user API")
    probe = commands.add_parser("probe", help="Run bounded, read-only core API checks")
    probe.add_argument(
        "--anonymous", action="store_true", help="Use no saved login (negative control)"
    )
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args))
    except BlackboardError as exc:
        report = exc.as_dict()
    except (KeyboardInterrupt, EOFError):
        report = {"state": "cancelled", "message": "Login cancelled."}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["state"] == "authenticated" else 2


if __name__ == "__main__":
    raise SystemExit(main())

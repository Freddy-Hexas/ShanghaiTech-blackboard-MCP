"""Streaming HTTP with the same private cookie jar used by API and file reads."""

from contextlib import asynccontextmanager
from http.cookiejar import Cookie, CookieJar

import httpx

from .client import Response
from .errors import BlackboardError


def cookie_jar(cookies: list[dict]) -> CookieJar:
    jar = CookieJar()
    for item in cookies:
        domain = item["domain"]
        expires = item.get("expires", -1)
        rest = {"SameSite": item.get("sameSite", "Lax")}
        if item.get("httpOnly"):
            rest["HttpOnly"] = None
        jar.set_cookie(
            Cookie(
                version=0,
                name=item["name"],
                value=item["value"],
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=domain.startswith("."),
                domain_initial_dot=domain.startswith("."),
                path=item["path"],
                path_specified=True,
                secure=item.get("secure", False),
                expires=None if expires == -1 else int(expires),
                discard=expires == -1,
                comment=None,
                comment_url=None,
                rest=rest,
                rfc2109=False,
            )
        )
    return jar


class HttpTransport:
    def __init__(self, cookies: list[dict], *, transport=None):
        self.http = httpx.AsyncClient(
            cookies=cookie_jar(cookies),
            follow_redirects=False,
            verify=True,
            headers={"Accept-Encoding": "identity"},
            transport=transport,
        )

    async def __aenter__(self):
        await self.http.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.http.__aexit__(*args)

    def cookies(self) -> list[dict]:
        self.http.cookies.jar.clear_expired_cookies()
        result = []
        for cookie in self.http.cookies.jar:
            rest = {key.lower(): value for key, value in cookie._rest.items()}
            same_site = str(rest.get("samesite", "Lax")).capitalize()
            result.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "expires": cookie.expires if cookie.expires is not None else -1,
                    "httpOnly": "httponly" in rest,
                    "secure": cookie.secure,
                    "sameSite": same_site if same_site in ("Strict", "Lax", "None") else "Lax",
                }
            )
        return result

    @asynccontextmanager
    async def stream(self, url: str, *, timeout_ms: float):
        try:
            async with self.http.stream("GET", url, timeout=timeout_ms / 1000) as response:
                yield response
        except httpx.HTTPError as exc:
            # HTTP exceptions may contain credential-bearing URLs and headers.
            raise BlackboardError(
                "network_error", "Request failed; check network and TLS."
            ) from exc

    async def get(self, url: str, *, timeout_ms: float) -> Response:
        async with self.stream(url, timeout_ms=timeout_ms) as response:
            chunks, size = [], 0
            async for chunk in response.aiter_bytes(65536):
                size += len(chunk)
                if size > 5 * 1024 * 1024:
                    raise BlackboardError("response_too_large", "Response exceeds 5 MiB.")
                chunks.append(chunk)
            return Response(response.status_code, dict(response.headers), b"".join(chunks))

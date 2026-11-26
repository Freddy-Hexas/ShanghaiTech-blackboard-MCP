"""Read Classic assignment file links and download only those authorized resources."""

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from .client import validate_id
from .errors import BlackboardError

RESOURCE = re.compile(r"^(/bbcswebdav/pid-([0-9]+)-dt-content-rid-[0-9]+_[0-9]+/)")
ATTACHMENT_ID = r"[0-9a-f]{32}"


@dataclass
class DownloadBudget:
    remaining: int


@dataclass(frozen=True)
class Attachment:
    id: str
    file_name: str
    url: str
    prefix: str

    def public(self):
        # URLs are re-discovered at download time, never accepted from the caller.
        return {"id": self.id, "file_name": self.file_name, "text_is_untrusted": True}


def file_url(
    base_url: str,
    value: str,
    content_id: str,
    prefix: str | None = None,
    relative_to: str | None = None,
):
    """Allow only the same origin and the assignment's exact WebDAV resource scope."""
    try:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 8192
            or value.startswith("//")
            or value != value.strip()
            or "\\" in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError("invalid URL")
        # Validate before urljoin can normalize literal dot segments away.
        raw_path = urlsplit(value).path
        if any(segment in (".", "..") for segment in raw_path.split("/")):
            raise ValueError("path traversal")
        absolute = urljoin(relative_to or base_url + "/", value)
        parsed, origin = urlsplit(absolute), urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != origin.hostname
            or (parsed.port or 443) != (origin.port or 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("origin or query")
        for segment in parsed.path.split("/"):
            for _ in range(4):
                if (
                    segment in (".", "..")
                    or "/" in segment
                    or "\\" in segment
                    or any(ord(c) < 32 or ord(c) == 127 for c in segment)
                ):
                    raise ValueError("path traversal")
                decoded = unquote(segment, errors="strict")
                if decoded == segment:
                    break
                segment = decoded
            else:
                raise ValueError("encoded path")
        match = RESOURCE.match(parsed.path)
        if not match or match[2] != validate_id(content_id).split("_")[1]:
            raise ValueError("assignment scope")
        if prefix is not None and match[1] != prefix:
            raise ValueError("attachment scope")
        return absolute, match[1]
    except (ValueError, TypeError, UnicodeError) as exc:
        raise BlackboardError(
            "unsafe_url", "Attachment link is outside its allowed scope."
        ) from exc


def looks_like_login(body: bytes) -> bool:
    soup = BeautifulSoup(body, "html.parser")
    return bool(soup.select('input[type="password"]')) or any(
        re.search(r"/(?:login|login\.jsp)(?:[?/#]|$)", form.get("action", ""), re.I)
        for form in soup.find_all("form")
    )


def parse_attachments(body: bytes, base_url: str, content_id: str):
    soup = BeautifulSoup(body, "html.parser")
    items = soup.find_all("li", id="contentListItem:" + content_id)
    if len(items) != 1:
        code = "login_required" if looks_like_login(body) else "unsupported_layout"
        raise BlackboardError(code, "Cannot identify the requested assignment on its content page.")
    attachments, rejected = {}, 0
    for anchor in items[0].select("a[href]"):
        href = anchor["href"]
        if "/bbcswebdav/" not in href:
            continue
        try:
            url, prefix = file_url(base_url, href, content_id)
        except BlackboardError:
            rejected += 1
            continue
        key = hashlib.sha256(prefix.encode()).hexdigest()[:32]
        file_name = anchor.get_text(" ", strip=True)[:250] or "attachment"
        attachments.setdefault(key, Attachment(key, file_name, url, prefix))
        if len(attachments) > 100:
            raise BlackboardError("attachment_limit", "More than 100 attachment links in one item.")
    return list(attachments.values()), rejected


async def discover(service, course_id, content_id):
    validate_id(content_id)
    tree = await service.get_content_tree(course_id)
    node = next((n for n in tree["contents"] if n["id"] == content_id), None)
    if node is None:
        code = "not_found" if service.complete else "incomplete"
        raise BlackboardError(code, "Assignment not found in the authorized course content.")
    if node["type"] != "resource/x-bb-assignment":
        raise BlackboardError("not_assignment", "This content item is not a Classic assignment.")
    if node["parent_id"] is None:
        raise BlackboardError("unsupported_layout", "Assignment parent folder is unavailable.")
    try:
        body = await service.client.content_page(course_id, node["parent_id"])
        attachments, rejected = parse_attachments(body, service.settings.base_url, content_id)
    except BlackboardError as exc:
        if exc.code == "login_required":
            service.client.authentication_failed = True
        raise
    if rejected:
        service.warn("unsupported_attachment_links", incomplete=True, count=rejected)
    return node, attachments


async def list_attachments(service, course_id, content_id):
    node, attachments = await discover(service, course_id, content_id)
    return {
        "course_id": course_id,
        "content_id": content_id,
        "title": node["title"],
        "source": "classic_content_page",
        "attachments": [a.public() for a in attachments],
        "count": len(attachments),
        "text_is_untrusted": True,
    }


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", value).strip(" .")
    # Bidi controls and invisible formatting characters are not useful in a local filename.
    value = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f]", "", value).strip(" .")
    if not value:
        value = "attachment"
    if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", value, re.I):
        value = "_" + value
    # Bound both Windows path length and UTF-8 filename bytes, keeping the extension.
    suffix = Path(value).suffix[:20]
    if len(value) > 90 or len(value.encode("utf-8")) > 210:
        stem = value[: -len(Path(value).suffix)] if suffix else value
        stem = stem[: 90 - len(suffix)].encode("utf-8")[: 210 - len(suffix.encode("utf-8"))]
        value = stem.decode("utf-8", errors="ignore").rstrip(" .") + suffix
    return value


def destination_dir(settings, account, course_id, content_id):
    root = settings.download_dir.absolute()
    # Refuse redirected roots/children rather than writing through a symlink or junction.
    if root.resolve() != root:
        raise BlackboardError("unsafe_path", "Download directory resolves through a link.")
    folder = root / account[:16] / course_id / content_id
    if folder.resolve() != folder:
        raise BlackboardError(
            "unsafe_path", "Download directory resolves outside its expected path."
        )
    folder.mkdir(parents=True, exist_ok=True)
    if folder.resolve() != folder:
        raise BlackboardError("unsafe_path", "Download directory changed during creation.")
    return folder


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def publish_download(temporary: Path, folder: Path, filename: str, checksum: str):
    """Reuse identical bytes or add a readable counter without replacing existing files."""
    name = Path(filename)
    for number in range(1, 1001):
        candidate = filename if number == 1 else f"{name.stem} ({number}){name.suffix}"
        destination = folder / candidate
        if destination.resolve() != destination or destination.is_symlink():
            raise BlackboardError("unsafe_path", "Refusing a linked destination file.")
        try:
            # Linking publishes the completed file atomically and never overwrites a collision.
            os.link(temporary, destination)
        except FileExistsError:
            if not destination.is_file():
                continue
            if (
                destination.stat().st_size == temporary.stat().st_size
                and file_digest(destination) == checksum
            ):
                return destination, True
        else:
            return destination, False
    raise BlackboardError("local_file_conflict", "Too many files with the same download name.")


async def download_one(client, attachment, content_id, folder, budget, course_name):
    url = attachment.url
    byte_limit = min(client.settings.max_download_bytes, budget.remaining)
    seen = set()
    temporary = None
    try:
        async with client._lock:
            for _ in range(5):
                url, _ = file_url(client.settings.base_url, url, content_id, attachment.prefix)
                if url in seen:
                    raise BlackboardError("redirect_loop", "Repeated attachment redirect.")
                seen.add(url)
                await client.pace_request()
                async with client.transport.stream(
                    url, timeout_ms=client.settings.timeout_seconds * 1000
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        target = response.headers.get("location")
                        if not target:
                            raise BlackboardError(
                                "invalid_response", "Download redirect has no target."
                            )
                        try:
                            target_path = urlsplit(target).path
                        except ValueError as exc:
                            raise BlackboardError(
                                "unsafe_url", "Malformed download redirect."
                            ) from exc
                        if re.search(r"/login(?:[/?;.]|$)", target_path, re.I):
                            client.authentication_failed = True
                            raise BlackboardError(
                                "login_required", "Download requires a new login."
                            )
                        # Validate origin/resource before the next request sends Cookies.
                        url, _ = file_url(
                            client.settings.base_url,
                            target,
                            content_id,
                            attachment.prefix,
                            relative_to=url,
                        )
                        continue
                    client.read_status(response.status_code)
                    length = response.headers.get("content-length")
                    if length is not None and (not length.isdigit() or int(length) > byte_limit):
                        raise BlackboardError(
                            "download_too_large", "File exceeds the download limit."
                        )
                    media_type = response.headers.get("content-type", "application/octet-stream")
                    media_type = media_type.split(";", 1)[0].lower().strip()
                    descriptor, name = tempfile.mkstemp(prefix=".part-", dir=folder)
                    temporary = Path(name)
                    digest, size, prefix = hashlib.sha256(), 0, b""
                    with os.fdopen(descriptor, "wb") as output:
                        async for chunk in response.aiter_bytes(65536):
                            size += len(chunk)
                            budget.remaining = max(0, budget.remaining - len(chunk))
                            if size > byte_limit:
                                raise BlackboardError(
                                    "download_too_large", "File exceeds the download limit."
                                )
                            if len(prefix) < 16384:
                                prefix += chunk[: 16384 - len(prefix)]
                            output.write(chunk)
                            digest.update(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    sniff = prefix.lstrip().lower()
                    if "html" in media_type or sniff.startswith(
                        (b"<!doctype html", b"<html", b"<form")
                    ):
                        if looks_like_login(prefix):
                            client.authentication_failed = True
                            raise BlackboardError(
                                "login_required", "Received a login page, not a file."
                            )
                        raise BlackboardError(
                            "unexpected_html", "Received HTML instead of an attachment."
                        )
                    if (
                        length is not None
                        and response.headers.get("content-encoding", "identity") == "identity"
                        and int(length) != size
                    ):
                        raise BlackboardError("incomplete_download", "File transfer ended early.")
                    checksum = digest.hexdigest()
                    course_prefix = safe_filename(course_name)[:40].rstrip(" .")
                    filename = safe_filename(f"{course_prefix}-{attachment.file_name}")
                    destination, reused = publish_download(temporary, folder, filename, checksum)
                    return {
                        **attachment.public(),
                        "saved_file_name": destination.name,
                        "path": str(destination),
                        "bytes": size,
                        "sha256": checksum,
                        "media_type": media_type,
                        "reused": reused,
                    }
            raise BlackboardError("redirect_limit", "Too many attachment redirects.")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def download_attachments(service, course_id, content_id, attachment_ids=None):
    if attachment_ids is not None and (
        not isinstance(attachment_ids, list)
        or not attachment_ids
        or any(not isinstance(i, str) or not re.fullmatch(ATTACHMENT_ID, i) for i in attachment_ids)
    ):
        raise BlackboardError(
            "invalid_id", "Use attachment IDs returned by list_assignment_attachments."
        )
    node, attachments = await discover(service, course_id, content_id)
    if attachment_ids is not None:
        requested = set(attachment_ids)
        if requested - {a.id for a in attachments}:
            raise BlackboardError("not_found", "Requested attachment is not in this assignment.")
        attachments = [a for a in attachments if a.id in requested]
    if len(attachments) > service.settings.max_download_files:
        raise BlackboardError("download_limit", "Select fewer attachment IDs for this download.")
    files, failed = [], []
    course_name = None
    budget = DownloadBudget(service.settings.max_total_download_bytes)
    try:
        folder = None
        if attachments:
            course = await service.get_course(course_id)
            course_name = course["name"] or course["course_code"] or course_id
            folder = destination_dir(
                service.settings, await service.account_key(), course_id, content_id
            )
        for index, attachment in enumerate(attachments):
            try:
                if budget.remaining <= 0:
                    raise BlackboardError("download_too_large", "Total download limit reached.")
                result = await download_one(
                    service.client,
                    attachment,
                    content_id,
                    folder,
                    budget,
                    course_name,
                )
                files.append(result)
            except (BlackboardError, OSError) as exc:
                code = exc.code if isinstance(exc, BlackboardError) else "download_io_error"
                failed.append({"id": attachment.id, "state": code})
                service.warn(code, incomplete=True, attachment_id=attachment.id)
                if code in {"login_required", "request_limit", "unsafe_path", "download_io_error"}:
                    failed.extend(
                        {"id": a.id, "state": "not_attempted"} for a in attachments[index + 1 :]
                    )
                    break
    except OSError as exc:
        raise BlackboardError("download_io_error", "Cannot create the download directory.") from exc
    return {
        "course_id": course_id,
        "course_name": course_name,
        "content_id": content_id,
        "title": node["title"],
        "files": files,
        "failed": failed,
        "requested_count": len(attachments),
        "downloaded_count": len(files),
        "bytes": sum(f["bytes"] for f in files),
        "storage": "mcp_server_local_filesystem",
        "text_is_untrusted": True,
    }

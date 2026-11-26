"""Durable per-account watch state with at-least-once delivery and explicit acknowledgement."""

import hashlib
import json
import os
import re
import secrets
import tempfile
from contextlib import contextmanager
from typing import Literal

from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, ValidationError

from .client import validate_id
from .errors import BlackboardError
from .models import now_iso


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def requested_scope(settings, course_ids, term_id) -> dict:
    if course_ids is not None:
        if term_id is not None or not course_ids or len(course_ids) > settings.max_courses:
            raise BlackboardError("invalid_scope", "Provide a bounded list of courses or one term.")
        return {"course_ids": sorted({validate_id(item) for item in course_ids})}
    term_id = term_id or settings.current_term_id
    if term_id is None:
        raise BlackboardError("scope_required", "Set current_term_id or specify courses/a term.")
    return {"term_id": validate_id(term_id)}


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fetched_at: str
    course_ids: list[str]
    assignments: dict[str, dict]
    materials: dict[str, dict]


class Pending(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str
    snapshot: Snapshot
    events: list[dict]


class WatchState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    account: str
    watcher: str
    scope: dict
    baseline: Snapshot
    pending: Pending | None = None
    last_acknowledged: str | None = None


def compact_snapshot(data: dict) -> Snapshot:
    assignments = {}
    for item in data["assignments"]:
        assignments[f"{item['course_id']}:{item['id']}"] = {
            key: item[key]
            for key in ("id", "course_id", "title", "url", "deadline", "description_sha256")
        }
    materials = {}
    for item in data["materials"]:
        materials[f"{item['course_id']}:{item['id']}"] = {
            **{
                key: item[key]
                for key in ("id", "course_id", "title", "url", "type", "description_sha256")
            },
            "attachments": sorted(item["attachments"], key=lambda entry: entry["id"]),
        }
    return Snapshot(
        fetched_at=now_iso(),
        course_ids=data["course_ids"],
        assignments=assignments,
        materials=materials,
    )


def deadline_signature(value: dict):
    return {
        "system": sorted(value["system_due_candidates"]),
        "description": sorted({item["at"] for item in value["description_candidates"]}),
        "unresolved": sorted(value["unresolved_expressions"]),
    }


def changes(before: Snapshot, after: Snapshot) -> list[dict]:
    events = []
    previous = digest(before.model_dump())

    def add(kind, key, old, new):
        events.append(
            {
                "id": digest([previous, kind, key, old, new]),
                "type": kind,
                "resource_key": key,
                "before": old,
                "after": new,
            }
        )

    for key, item in after.assignments.items():
        old = before.assignments.get(key)
        if old is None:
            add("new_assignment", key, None, item)
        elif deadline_signature(old["deadline"]) != deadline_signature(item["deadline"]):
            add("deadline_changed", key, old, item)
        elif old != item:
            add("assignment_updated", key, old, item)
    for key in before.assignments.keys() - after.assignments.keys():
        add("assignment_removed", key, before.assignments[key], None)
    for key, item in after.materials.items():
        old = before.materials.get(key)
        if old is None:
            add("new_material", key, None, item)
            continue
        old_files = {entry["id"]: entry for entry in old["attachments"]}
        new_files = {entry["id"]: entry for entry in item["attachments"]}
        for file_id in new_files.keys() - old_files.keys():
            add(
                "new_attachment",
                f"{key}:{file_id}",
                None,
                {
                    **new_files[file_id],
                    "content_id": item["id"],
                    "course_id": item["course_id"],
                    "title": item["title"],
                    "url": item["url"],
                },
            )
        # Include replacement/rename/removal or body changes, beyond simply added files.
        old_metadata = {k: v for k, v in old.items() if k != "attachments"}
        new_metadata = {k: v for k, v in item.items() if k != "attachments"}
        if old_metadata != new_metadata or any(new_files.get(k) != v for k, v in old_files.items()):
            add("material_updated", key, old, item)
    for key in before.materials.keys() - after.materials.keys():
        add("material_removed", key, before.materials[key], None)
    return sorted(events, key=lambda item: (item["resource_key"], item["type"]))


class MonitorStore:
    def __init__(self, profile_dir):
        self.directory = profile_dir / "monitoring"

    @staticmethod
    def validate_watcher(watcher):
        if not isinstance(watcher, str) or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", watcher
        ):
            raise BlackboardError(
                "invalid_watcher", "Use 1-64 ASCII letters, digits, underscores or hyphens."
            )

    @contextmanager
    def transaction(self, account, watcher):
        self.validate_watcher(watcher)
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = self.directory / (digest([account, watcher]) + ".json")
            with FileLock(self.directory / ".monitor.lock", timeout=0):
                state = None
                if path.exists():
                    if path.stat().st_size > 32 * 1024 * 1024:
                        raise ValueError("Oversized state")
                    state = WatchState.model_validate_json(path.read_bytes())
                    if state.account != account or state.watcher != watcher:
                        raise ValueError("State identity mismatch")
                yield path, state
        except Timeout as exc:
            raise BlackboardError(
                "monitor_busy", "Another monitor operation is running; retry later."
            ) from exc
        except (OSError, ValueError, ValidationError) as exc:
            raise BlackboardError(
                "monitor_state_error", "Cannot safely read or save watch state."
            ) from exc

    @staticmethod
    def write(path, state):
        temporary = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=".monitor-", dir=path.parent)
            temporary = path.parent / name
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(state.model_dump_json())
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def ensure_scope(state, scope):
        if state is not None and state.scope != scope:
            raise BlackboardError(
                "watcher_scope_changed", "Use a new watcher_id for a different scope."
            )

    @staticmethod
    def pending_result(state):
        return {
            "watcher_id": state.watcher,
            "monitor_state": "pending_acknowledgement",
            "scope": state.scope,
            "batch_token": state.pending.token,
            "baseline_at": state.baseline.fetched_at,
            "snapshot_at": state.pending.snapshot.fetched_at,
            "events": state.pending.events,
            "delivery_semantics": "at_least_once",
        }

    def pending(self, account, watcher, scope):
        with self.transaction(account, watcher) as (_, state):
            self.ensure_scope(state, scope)
            return self.pending_result(state) if state and state.pending else None

    def record(self, account, watcher, scope, snapshot: Snapshot, *, complete: bool):
        if not complete:
            raise BlackboardError("incomplete", "Incomplete scan; watch baseline has not advanced.")
        with self.transaction(account, watcher) as (path, state):
            self.ensure_scope(state, scope)
            if state is None:
                state = WatchState(account=account, watcher=watcher, scope=scope, baseline=snapshot)
                self.write(path, state)
                return {
                    "watcher_id": watcher,
                    "scope": scope,
                    "monitor_state": "baseline_created",
                    "snapshot_at": snapshot.fetched_at,
                    "events": [],
                    "batch_token": None,
                    "counts": {
                        "assignments": len(snapshot.assignments),
                        "materials": len(snapshot.materials),
                        "attachments": sum(
                            len(item["attachments"]) for item in snapshot.materials.values()
                        ),
                    },
                }
            if state.pending:
                return self.pending_result(state)
            events = changes(state.baseline, snapshot)
            if not events:
                state.baseline = snapshot
                self.write(path, state)
                return {
                    "watcher_id": watcher,
                    "scope": scope,
                    "monitor_state": "no_changes",
                    "snapshot_at": snapshot.fetched_at,
                    "events": [],
                    "batch_token": None,
                }
            state.pending = Pending(token=secrets.token_hex(24), snapshot=snapshot, events=events)
            self.write(path, state)
            return self.pending_result(state)

    def acknowledge(self, account, watcher, token):
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{48}", token):
            raise BlackboardError("invalid_batch_token", "Use the exact token from check_updates.")
        with self.transaction(account, watcher) as (path, state):
            if state is not None and token == state.last_acknowledged:
                return {"watcher_id": watcher, "monitor_state": "already_acknowledged"}
            if state is None or state.pending is None or state.pending.token != token:
                raise BlackboardError(
                    "stale_batch_token", "No matching pending batch for this account/watcher."
                )
            state.baseline = state.pending.snapshot
            state.pending = None
            state.last_acknowledged = token
            self.write(path, state)
            return {
                "watcher_id": watcher,
                "monitor_state": "acknowledged",
                "baseline_at": state.baseline.fetched_at,
            }


async def check_updates(service, watcher_id, course_ids=None, term_id=None):
    scope = requested_scope(service.settings, course_ids, term_id)
    MonitorStore.validate_watcher(watcher_id)
    account = await service.account_key()
    if "course_ids" in scope:
        for course_id in scope["course_ids"]:
            await service.authorize(course_id)
    store = MonitorStore(service.settings.profile_dir)
    pending = store.pending(account, watcher_id, scope)
    coverage = {
        "assignments": "content_and_gradebook_metadata",
        "materials": "documents_files_links_and_supported_attachment_metadata",
        "assignment_attachments": "not_monitored_use_list_assignment_attachments",
        "file_bytes": "not_downloaded_or_compared",
        "submission_and_personal_grade": "not_queried",
    }
    if pending is not None:
        return {**pending, "replayed": True, "coverage": coverage}
    snapshot = await service.snapshot(**scope)
    if not snapshot["course_ids"]:
        raise BlackboardError(
            "empty_scope", "No courses in watch scope; verify the configured term."
        )
    return {
        **store.record(
            account, watcher_id, scope, compact_snapshot(snapshot), complete=service.complete
        ),
        "replayed": False,
        "coverage": coverage,
    }


async def acknowledge_updates(service, watcher_id, batch_token):
    account = await service.account_key()
    return MonitorStore(service.settings.profile_dir).acknowledge(account, watcher_id, batch_token)

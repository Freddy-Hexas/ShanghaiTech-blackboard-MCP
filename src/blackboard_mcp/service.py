"""Authorized, bounded Blackboard reads and normalized course data."""

import hashlib
from datetime import UTC, datetime, timedelta

from .attachments import download_attachments, list_attachments
from .client import BlackboardClient, validate_id
from .deadlines import describe_deadline, parse_instant, plain_text
from .errors import BlackboardError

ASSIGNMENT = "resource/x-bb-assignment"
MATERIAL_TYPES = {"resource/x-bb-document", "resource/x-bb-file", "resource/x-bb-externallink"}
ATTACHMENT_TYPES = {"resource/x-bb-document", "resource/x-bb-file"}
FATAL = {"login_required", "request_limit", "unsafe_url", "session_error"}


class BlackboardService:
    def __init__(self, client: BlackboardClient):
        self.client = client
        self.settings = client.settings
        self.complete = True
        self.warnings = []
        self._memberships = None
        self._courses = {}
        self._trees = {}

    def warn(self, code: str, *, incomplete=False, **context):
        self.warnings.append({"code": code, **context})
        if incomplete:
            self.complete = False

    async def memberships(self) -> list[dict]:
        if self._memberships is None:
            self._memberships = await self.client.memberships()
        return self._memberships

    async def account_key(self) -> str:
        users = {row.get("userId") for row in await self.memberships()}
        if len(users) != 1 or not isinstance(next(iter(users)), str):
            raise BlackboardError(
                "account_unknown", "Cannot isolate monitoring state for this account."
            )
        user_id = validate_id(next(iter(users)))
        return hashlib.sha256(f"{self.settings.base_url}|{user_id}".encode()).hexdigest()

    async def authorize(self, course_id: str):
        validate_id(course_id)
        if course_id not in {row["courseId"] for row in await self.memberships()}:
            raise BlackboardError("access_denied", "Course is not in current user's memberships.")

    async def get_connection_status(self) -> dict:
        memberships = await self.memberships()
        return {
            "authenticated": True,
            "membership_count": len(memberships),
            "transport": "httpx_cookie_session",
            "current_term_id": self.settings.current_term_id,
            "session_lifetime": "server_controlled_unknown",
            "login_command": "blackboard-probe login",
        }

    async def get_course(self, course_id: str) -> dict:
        await self.authorize(course_id)
        if course_id not in self._courses:
            raw = await self.client.get(f"/courses/{course_id}")
            if raw.get("id") != course_id:
                raise BlackboardError("invalid_response", "Course identity does not match request.")
            term = raw.get("termId")
            if term is not None:
                validate_id(term)
            self._courses[course_id] = {
                "id": course_id,
                "course_code": raw.get("courseId"),
                "name": plain_text(raw.get("name")),
                "description": plain_text(raw.get("description"))[:12000],
                "term_id": term,
                "availability": raw.get("availability", {}).get("available", "Unknown"),
                "url": self.settings.base_url
                + "/webapps/blackboard/execute/launcher?"
                + f"type=Course&id={course_id}&url=",
            }
        return self._courses[course_id]

    async def list_courses(self, term_id: str | None = None) -> dict:
        if term_id is not None:
            validate_id(term_id)
        results = []
        for course_id in dict.fromkeys(row["courseId"] for row in await self.memberships()):
            try:
                course = await self.get_course(course_id)
                if term_id is None or course["term_id"] == term_id:
                    results.append(course)
                if term_id is not None and course["term_id"] is None:
                    self.warn("missing_term_id", incomplete=True, course_id=course_id)
            except BlackboardError as exc:
                if exc.code in FATAL:
                    raise
                self.warn(exc.code, incomplete=True, course_id=course_id)
        return {"courses": results, "term_id": term_id, "count": len(results)}

    async def search_courses(self, query: str) -> dict:
        query = query.strip()
        if not query or len(query) > 200:
            raise BlackboardError("invalid_query", "Use a search query of 1 to 200 characters.")
        courses = (await self.list_courses())["courses"]
        matched = [
            course
            for course in courses
            if query.casefold() in f"{course['name']} {course['course_code'] or ''}".casefold()
        ]
        return {"courses": matched, "count": len(matched)}

    def content(self, raw: dict, course_id: str, parent_id=None, depth=0) -> dict:
        content_id = validate_id(raw.get("id"))
        parent_id = parent_id if parent_id is not None else raw.get("parentId")
        if parent_id is not None:
            validate_id(parent_id)
        handler = raw.get("contentHandler")
        if not isinstance(handler, dict) or not isinstance(handler.get("id"), str):
            raise BlackboardError("invalid_response", "Missing content type.")
        has_children = raw.get("hasChildren", False)
        if not isinstance(has_children, bool):
            raise BlackboardError("invalid_response", "Invalid content children flag.")
        body = plain_text(raw.get("body"))
        if len(body) > 12000:
            self.warn(
                "description_truncated", incomplete=True, course_id=course_id, content_id=content_id
            )
        return {
            "id": content_id,
            "course_id": course_id,
            "parent_id": parent_id,
            "depth": depth,
            "title": plain_text(raw.get("title")),
            "type": handler["id"],
            "has_children": has_children,
            "description": body[:12000],
            "description_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "created": raw.get("created"),
            "modified": raw.get("modified"),
            "url": self.settings.base_url
            + "/webapps/blackboard/content/listContent.jsp?"
            + f"course_id={course_id}&content_id={content_id}",
            "text_is_untrusted": True,
        }

    async def list_course_contents(self, course_id: str) -> dict:
        await self.authorize(course_id)
        raw = await self.client.get_all(f"/courses/{course_id}/contents")
        nodes = [self.content(item, course_id) for item in raw[: self.settings.max_nodes]]
        if len(raw) > self.settings.max_nodes:
            self.warn("node_limit", incomplete=True, course_id=course_id)
        return {"course_id": course_id, "contents": nodes, "scope": "top_level"}

    async def get_content_tree(self, course_id: str) -> dict:
        await self.authorize(course_id)
        if course_id in self._trees:
            return self._trees[course_id]
        nodes = []
        visited = set()

        async def visit(endpoint, parent_id=None, depth=0):
            rows = await self.client.get_all(endpoint)
            for raw in rows:
                if len(nodes) >= self.settings.max_nodes:
                    self.warn("node_limit", incomplete=True, course_id=course_id)
                    return
                node = self.content(raw, course_id, parent_id, depth)
                if node["id"] in visited:
                    self.warn(
                        "repeated_content_id",
                        incomplete=True,
                        course_id=course_id,
                        content_id=node["id"],
                    )
                    continue
                visited.add(node["id"])
                nodes.append(node)
                if node["has_children"]:
                    if depth >= self.settings.max_depth:
                        self.warn(
                            "depth_limit",
                            incomplete=True,
                            course_id=course_id,
                            content_id=node["id"],
                        )
                        continue
                    try:
                        await visit(
                            f"/courses/{course_id}/contents/{node['id']}/children",
                            node["id"],
                            depth + 1,
                        )
                    except BlackboardError as exc:
                        if exc.code in FATAL:
                            raise
                        self.warn(
                            exc.code, incomplete=True, course_id=course_id, content_id=node["id"]
                        )

        await visit(f"/courses/{course_id}/contents")
        result = {"course_id": course_id, "contents": nodes, "scope": "recursive"}
        self._trees[course_id] = result
        return result

    async def scope(self, course_ids: list[str] | None, term_id: str | None) -> list[str]:
        if course_ids is not None and term_id is not None:
            raise BlackboardError("invalid_scope", "Specify courses or a term, not both.")
        if course_ids is not None:
            if not course_ids:
                raise BlackboardError("invalid_scope", "Provide at least one course ID.")
            selected = sorted(set(course_ids))
            for course_id in selected:
                await self.authorize(course_id)
        else:
            term_id = term_id or self.settings.current_term_id
            if term_id is None:
                raise BlackboardError(
                    "scope_required", "Set current_term_id or specify courses/a term."
                )
            selected = sorted(
                course["id"] for course in (await self.list_courses(term_id))["courses"]
            )
        if len(selected) > self.settings.max_courses:
            raise BlackboardError("course_limit", "Too many courses; use a smaller explicit scope.")
        return selected

    async def _course_assignments(self, course_id: str) -> list[dict]:
        course = await self.get_course(course_id)
        nodes = (await self.get_content_tree(course_id))["contents"]
        assignments = [node for node in nodes if node["type"] == ASSIGNMENT]
        if not assignments:
            return []
        columns = await self.client.get_all(f"/courses/{course_id}/gradebook/columns")
        for column in columns:
            validate_id(column.get("id"))
            if column.get("contentId") is not None:
                validate_id(column["contentId"])
        results = []
        for content in assignments:
            linked = [column for column in columns if column.get("contentId") == content["id"]]
            if len(linked) != 1:
                self.warn(
                    "missing_gradebook_column" if not linked else "multiple_gradebook_columns",
                    incomplete=not linked,
                    course_id=course_id,
                    content_id=content["id"],
                )
            due_values = []
            summaries = []
            for column in linked:
                grading = column.get("grading") or {}
                score = column.get("score") or {}
                if not isinstance(grading, dict) or not isinstance(score, dict):
                    raise BlackboardError("invalid_response", "Invalid grading metadata.")
                due = grading.get("due")
                if due is not None:
                    due_values.append(due)
                summaries.append(
                    {"id": column["id"], "due": due, "possible_score": score.get("possible")}
                )
            deadline = describe_deadline(due_values, content["description"], self.settings.timezone)
            for code in deadline["warnings"]:
                self.warn(code, course_id=course_id, content_id=content["id"])
            results.append(
                {
                    **content,
                    "course_name": course["name"],
                    "gradebook_columns": summaries,
                    "deadline": deadline,
                    "submission_status": "unknown",
                    "student_score": None,
                    "attachments_state": "not_loaded",
                    "attachments_tool": "list_assignment_attachments",
                }
            )
        return results

    async def list_assignments(self, course_id=None, term_id=None) -> dict:
        selected = await self.scope([course_id] if course_id else None, term_id)
        results = []
        for selected_id in selected:
            try:
                results.extend(await self._course_assignments(selected_id))
            except BlackboardError as exc:
                if exc.code in FATAL:
                    raise
                self.warn(exc.code, incomplete=True, course_id=selected_id)
        return {"assignments": results, "course_ids": selected, "count": len(results)}

    async def get_assignment(self, course_id: str, content_id: str) -> dict:
        validate_id(content_id)
        for item in await self._course_assignments(course_id):
            if item["id"] == content_id:
                return item
        code = "not_found" if self.complete else "incomplete"
        raise BlackboardError(code, "Assignment not found in the scanned course content.")

    async def list_assignment_attachments(self, course_id: str, content_id: str) -> dict:
        return await list_attachments(self, course_id, content_id)

    async def download_assignment_attachments(
        self, course_id: str, content_id: str, attachment_ids: list[str] | None = None
    ) -> dict:
        return await download_attachments(self, course_id, content_id, attachment_ids)

    async def get_upcoming_deadlines(self, days=7, course_id=None, term_id=None) -> dict:
        if type(days) is not int or not 1 <= days <= 365:
            raise BlackboardError("invalid_query", "days must be between 1 and 365.")
        result = await self.list_assignments(course_id, term_id)
        now = datetime.now(UTC)
        end = now + timedelta(days=days)
        upcoming, overdue, undated = [], [], []
        for assignment in result["assignments"]:
            at = assignment["deadline"]["reminder_at"]
            if at is None:
                undated.append(assignment)
            elif parse_instant(at) < now:
                overdue.append(assignment)
            elif parse_instant(at) <= end:
                upcoming.append(assignment)
        upcoming.sort(key=lambda item: item["deadline"]["reminder_at"])
        return {
            "from": now.isoformat(),
            "to": end.isoformat(),
            "assignments": upcoming,
            "past_due_submission_unknown": overdue,
            "undated_or_unresolved": undated,
            "course_ids": result["course_ids"],
        }

    async def list_materials(self, course_id: str) -> dict:
        nodes = (await self.get_content_tree(course_id))["contents"]
        materials = []
        for node in nodes:
            if node["type"] not in MATERIAL_TYPES:
                continue
            item = {**node, "attachments": [], "attachments_state": "not_applicable"}
            if node["type"] in ATTACHMENT_TYPES:
                try:
                    attachments = await self.client.get_all(
                        f"/courses/{course_id}/contents/{node['id']}/attachments"
                    )
                    for attachment in attachments:
                        validate_id(attachment.get("id"))
                        if not isinstance(attachment.get("fileName"), str):
                            raise BlackboardError(
                                "invalid_response", "Missing attachment filename."
                            )
                    item["attachments"] = [
                        {"id": att["id"], "file_name": att["fileName"]} for att in attachments
                    ]
                    item["attachments_state"] = "ok"
                except BlackboardError as exc:
                    if exc.code in FATAL:
                        raise
                    item["attachments_state"] = exc.code
                    self.warn(exc.code, incomplete=True, course_id=course_id, content_id=node["id"])
            materials.append(item)
        return {"course_id": course_id, "materials": materials, "count": len(materials)}

    async def snapshot(self, course_ids=None, term_id=None) -> dict:
        selected = await self.scope(course_ids, term_id)
        assignments, materials = [], []
        for course_id in selected:
            try:
                assignments.extend(await self._course_assignments(course_id))
                materials.extend((await self.list_materials(course_id))["materials"])
                for node in self._trees[course_id]["contents"]:
                    if node["type"] not in MATERIAL_TYPES | {ASSIGNMENT, "resource/x-bb-folder"}:
                        self.warn(
                            "unsupported_content_type",
                            incomplete=True,
                            course_id=course_id,
                            content_id=node["id"],
                            content_type=node["type"],
                        )
            except BlackboardError as exc:
                if exc.code in FATAL:
                    raise
                self.warn(exc.code, incomplete=True, course_id=course_id)
        return {"course_ids": selected, "assignments": assignments, "materials": materials}

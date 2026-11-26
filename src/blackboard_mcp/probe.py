"""A bounded feasibility check that returns counts, never raw personal data."""

from collections import Counter
from datetime import UTC, datetime

from .client import BlackboardClient, validate_id
from .config import Settings
from .errors import BlackboardError


async def run_probe(client: BlackboardClient, settings: Settings) -> dict:
    report = {
        "source": "blackboard",
        "scope": "configured_core_api_sample",
        "fetched_at": datetime.now(UTC).isoformat(),
        "complete": False,
        "checks": [],
    }
    checks = report["checks"]
    try:
        version = await client.get("/system/version")
        learn = version.get("learn")
        if not isinstance(learn, dict) or not all(
            type(learn.get(key)) is int for key in ("major", "minor", "patch")
        ):
            raise BlackboardError("invalid_response", "Expected Blackboard version information.")
        checks.append(
            {
                "check": "system_version",
                "state": "ok",
                "version": ".".join(str(learn[key]) for key in ("major", "minor", "patch")),
            }
        )
        memberships = await client.memberships()
        checks.append({"check": "memberships", "state": "ok", "count": len(memberships)})
        if settings.course_id:
            course_id = validate_id(settings.course_id)
            if course_id not in {row["courseId"] for row in memberships}:
                raise BlackboardError("access_denied", "Probe course is not in your memberships.")
            path = f"/courses/{course_id}"
            course = await client.get(path)
            if course.get("id") != course_id:
                raise BlackboardError("invalid_response", "Course identity does not match request.")
            checks.append(
                {"check": "course", "state": "ok", "has_term_id": bool(course.get("termId"))}
            )
            contents = await client.get_all(path + "/contents")
            for item in contents:
                validate_id(item.get("id"))
            checks.append({"check": "contents", "state": "ok", "count": len(contents)})
            parent_id = settings.parent_content_id
            if parent_id:
                validate_id(parent_id)
                if parent_id not in {item.get("id") for item in contents}:
                    raise BlackboardError(
                        "invalid_probe", "Probe parent must be an observed top-level content item."
                    )
            else:
                parent_id = next(
                    (item.get("id") for item in contents if item.get("hasChildren")), None
                )
            children = None
            if parent_id:
                validate_id(parent_id)
                children = await client.get_all(path + f"/contents/{parent_id}/children")
                for item in children:
                    validate_id(item.get("id"))
                checks.append({"check": "children", "state": "ok", "count": len(children)})
            else:
                checks.append({"check": "children", "state": "not_applicable"})
            columns = await client.get_all(path + "/gradebook/columns")
            for column in columns:
                validate_id(column.get("id"))
                if column.get("contentId") is not None:
                    validate_id(column["contentId"])
            linked = Counter(
                column["contentId"]
                for column in columns
                if isinstance(column.get("contentId"), str)
            )
            items = contents + (children or [])
            assignments = [
                item
                for item in items
                if isinstance(item.get("contentHandler"), dict)
                and item["contentHandler"].get("id") == "resource/x-bb-assignment"
            ]
            checks.append(
                {
                    "check": "gradebook_columns",
                    "state": "ok",
                    "count": len(columns),
                    "sample_assignment_count": len(assignments),
                    "sample_matched_assignments": sum(
                        linked[item.get("id")] > 0 for item in assignments
                    ),
                    "sample_multiple_matches": sum(
                        linked[item.get("id")] > 1 for item in assignments
                    ),
                }
            )
        report.update(state="authenticated", complete=True)
    except BlackboardError as exc:
        report.update(exc.as_dict())
    return report

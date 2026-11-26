"""Conservative date extraction; retain conflicts instead of guessing a due date."""

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .errors import BlackboardError


def plain_text(value: str | None) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BlackboardError("invalid_response", "Expected text in content metadata.")
    soup = BeautifulSoup(value, "html.parser")
    for element in soup(["script", "style", "iframe", "object"]):
        element.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def parse_instant(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("Missing timezone")
        return result.astimezone(UTC)
    except (ValueError, TypeError, AttributeError) as exc:
        raise BlackboardError(
            "invalid_response", "Invalid or timezone-free API timestamp."
        ) from exc


MONTHS = {
    name: index
    for index, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
DATE = re.compile(
    r"(?<!\d)(?:(?P<year>20\d{2})\s*[年/-]\s*)?"
    r"(?P<month>\d{1,2})\s*[月/-]\s*(?P<day>\d{1,2})(?!\d)(?:日|号)?"
    r"|\b(?P<english>Jan\w*|Feb\w*|Mar\w*|Apr\w*|May|Jun\w*|Jul\w*|Aug\w*|Sep\w*|Oct\w*|Nov\w*|Dec\w*)"
    r"\.?\s+(?P<english_day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<english_year>20\d{2}))?",
    re.IGNORECASE,
)
TIME = re.compile(
    r"^\s*[）),，]?\s*(?:[（(][^）)]{1,12}[）)]\s*)?(?:at\s+|T)?"
    r"(?P<period>上午|下午|晚上|早上|中午|凌晨)?\s*"
    r"(?P<hour>\d{1,2})(?:(?:[:：](?P<minute>\d{2}))|(?:点(?P<cnminute>\d{1,2})?分?))?"
    r"\s*(?P<ampm>a\.?m\.?|p\.?m\.?)?",
    re.IGNORECASE,
)
CUE = re.compile(r"\b(?:due|deadline|before)\b|截止|截至|提交|上交", re.IGNORECASE)


def describe_deadline(due_values: list[str], text: str, timezone: str) -> dict:
    zone = ZoneInfo(timezone)
    system_dates = sorted({parse_instant(value) for value in due_values})
    anchor = system_dates[0].astimezone(zone) if len(system_dates) == 1 else None
    candidates = []
    unresolved = []
    for date in DATE.finditer(text):
        time = TIME.match(text[date.end() :])
        if not time:
            unresolved.append(date.group(0))
            continue
        month = MONTHS[date["english"][:3].lower()] if date["english"] else int(date["month"])
        day = int(date["english_day"] or date["day"])
        year = date["english_year"] or date["year"]
        inferred = not year
        raw = text[date.start() : date.end() + time.end()]
        context = text[max(0, date.start() - 60) : date.end() + time.end() + 10]
        if not CUE.search(context):
            unresolved.append(raw)
            continue
        if not year:
            if anchor is None or (anchor.month, anchor.day) != (month, day):
                unresolved.append(raw)
                continue
            year = anchor.year
        hour = int(time["hour"])
        minute = int(time["minute"] or time["cnminute"] or 0)
        ampm = (time["ampm"] or "").replace(".", "").lower()
        period = time["period"]
        if ampm or period:
            if not 1 <= hour <= 12 or (period in ("晚上", "中午") and hour == 12):
                unresolved.append(raw)
                continue
            hour = hour % 12 + (12 if ampm == "pm" or period in ("下午", "晚上", "中午") else 0)
        elif hour <= 12 or (time["minute"] is None and time["cnminute"] is None):
            # A bare '10'/'10:00' in prose may mean morning or evening.
            unresolved.append(raw)
            continue
        try:
            instant = datetime(int(year), month, day, hour, minute, tzinfo=zone)
        except ValueError:
            unresolved.append(raw)
            continue
        # Explicit foreign timezones/offsets need review instead of silently using Shanghai.
        suffix = text[date.end() + time.end() :][:20]
        if re.match(r"\s*(?:Z\b|[+-]\d\d:?\d\d|UTC|GMT|PST|PDT|EST|EDT)", suffix, re.I):
            unresolved.append(raw + suffix)
            continue
        candidates.append(
            {
                "at": instant.astimezone(UTC).isoformat(),
                "local": instant.isoformat(),
                "evidence": raw,
                "year_inferred_from_system": inferred,
            }
        )
    description_dates = {parse_instant(item["at"]) for item in candidates}
    warnings = []
    if len(system_dates) > 1:
        warnings.append("multiple_system_deadlines")
    if unresolved or len(description_dates) > 1:
        warnings.append("description_deadline_needs_review")
    conflict = bool(system_dates and description_dates and set(system_dates) != description_dates)
    if conflict:
        warnings.append("deadline_conflict")
    # Earliest definite value is a reminder, never a replacement of Blackboard's date.
    reminder_dates = set(system_dates)
    if len(description_dates) == 1 and not unresolved:
        reminder_dates |= description_dates
    reminder = min(reminder_dates) if reminder_dates else None
    return {
        "system_due_at": system_dates[0].isoformat() if len(system_dates) == 1 else None,
        "system_due_local": anchor.isoformat() if anchor else None,
        "system_due_candidates": [date.isoformat() for date in system_dates],
        "description_candidates": candidates,
        "unresolved_expressions": unresolved,
        "conflict": conflict,
        "needs_review": bool(warnings),
        "reminder_at": reminder.isoformat() if reminder else None,
        "reminder_local": reminder.astimezone(zone).isoformat() if reminder else None,
        "timezone": timezone,
        "warnings": warnings,
    }

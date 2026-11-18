from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    """Serialize as ISO-8601 UTC with millisecond precision and a ``Z`` suffix."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_provider_datetime(value: object) -> datetime | None:
    """Parse DataForSEO datetimes ("2026-07-22 17:54:30 +00:00" — docs also show dashes in the time)."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    candidates = [text]
    parts = text.split(" ")
    if len(parts) == 3 and parts[1].count("-") == 2:
        candidates.append(f"{parts[0]} {parts[1].replace('-', ':')} {parts[2]}")
    for candidate in candidates:
        for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(candidate, fmt)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None

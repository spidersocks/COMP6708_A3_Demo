"""Canonical data-source catalog for tool provenance in the inspector.

Every specialist tool declares which stores/APIs it *may* read. At runtime we
emit which ones were actually consulted this turn so the System inspector can
show HKO vs SQLite vs Chroma vs CSV clearly — not as prose buried in a detail.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


DATA_SOURCES: Dict[str, Dict[str, Any]] = {
    "hko_opendata": {
        "id": "hko_opendata",
        "label": "HKO Open Data API",
        "kind": "http_api",
        "locator": "https://data.weather.gov.hk/weatherAPI/opendata/weather.php",
        "blurb": "Live typhoon / rainstorm warning codes from Hong Kong Observatory",
    },
    "centre_weather_json": {
        "id": "centre_weather_json",
        "label": "Centre weather policy",
        "kind": "json_file",
        "locator": "backend/facts/severe_weather.json",
        "blurb": "BrightNest T8 / black-rain suspension rules",
    },
    "hours_calendar": {
        "id": "hours_calendar",
        "label": "Opening-hours calendar",
        "kind": "rules_engine",
        "locator": "weekday windows + HK public holidays",
        "blurb": "Deterministic open/closed for a resolved date (optionally + live HKO)",
    },
    "class_register_csv": {
        "id": "class_register_csv",
        "label": "Class register (CSV)",
        "kind": "csv_store",
        "locator": "schedule_sheets / classes register",
        "blurb": "Live seat counts and timetable rows for MyCalendar + seat browse",
    },
    "bookings_sqlite": {
        "id": "bookings_sqlite",
        "label": "Bookings SQLite",
        "kind": "sqlite",
        "locator": "data/bookings.db",
        "blurb": "Sticky booking / assessment dialogue state and confirmed seats",
    },
    "families_csv": {
        "id": "families_csv",
        "label": "Families roster",
        "kind": "csv_store",
        "locator": "families.csv",
        "blurb": "Phone → parent → children identity for WhatsApp-shaped demo",
    },
    "chroma_kb": {
        "id": "chroma_kb",
        "label": "Chroma vector KB",
        "kind": "vector_index",
        "locator": "data/chroma · content/** Markdown",
        "blurb": "Dense retrieve over centre docs (fees, programmes, policies, …)",
    },
    "bm25_index": {
        "id": "bm25_index",
        "label": "BM25 sparse index",
        "kind": "sparse_index",
        "locator": "in-process Okapi BM25 over same chunks",
        "blurb": "Exact-term supplements in the channel pack",
    },
    "admin_todos": {
        "id": "admin_todos",
        "label": "SheetPro follow-ups",
        "kind": "sheet_store",
        "locator": "admin_todos",
        "blurb": "Staff research / assessment request rows",
    },
    "staff_messages": {
        "id": "staff_messages",
        "label": "MailBox inbox",
        "kind": "mail_store",
        "locator": "staff_messages",
        "blurb": "Urgent escalation emails for the centre desk",
    },
    "cancellations_csv": {
        "id": "cancellations_csv",
        "label": "Cancellations sheet",
        "kind": "csv_store",
        "locator": "cancellations.csv",
        "blurb": "Confirmed class cancellations after final check",
    },
}

# Default reads declared per tool (may-read). Runtime marks which fired.
TOOL_READS: Dict[str, List[str]] = {
    "severe_weather": ["centre_weather_json", "hko_opendata", "chroma_kb", "bm25_index"],
    "opening_hours": ["hours_calendar", "hko_opendata", "chroma_kb", "bm25_index"],
    "deterministic_opening_hours": ["hours_calendar", "hko_opendata", "chroma_kb", "bm25_index"],
    "booking_tool": ["class_register_csv", "families_csv", "chroma_kb", "bm25_index"],
    "booking_dialogue": ["bookings_sqlite", "class_register_csv", "families_csv", "admin_todos"],
    "cancel_dialogue": ["bookings_sqlite", "class_register_csv", "families_csv", "cancellations_csv"],
    "staff_escalation": ["staff_messages", "families_csv"],
    "rag": ["chroma_kb", "bm25_index"],
    "off_domain": [],
}


def catalog_public() -> List[Dict[str, Any]]:
    return [dict(v) for v in DATA_SOURCES.values()]


def describe(source_id: str) -> Dict[str, Any]:
    return dict(DATA_SOURCES.get(source_id) or {"id": source_id, "label": source_id})


def mark(
    source_id: str,
    *,
    status: str = "consulted",
    detail: str = "",
) -> Dict[str, Any]:
    base = describe(source_id)
    return {
        **base,
        "status": status,
        "detail": detail,
    }


def for_tool(route: str) -> List[Dict[str, Any]]:
    """Declared may-read list for a tool (not yet runtime status)."""
    return [describe(sid) for sid in TOOL_READS.get(route, []) if sid in DATA_SOURCES]


def build_turn_sources(
    *,
    route: str,
    consulted: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Inspector payload: declared reads + what this turn actually hit."""
    declared = for_tool(route)
    used = list(consulted or [])
    return {
        "declared": declared,
        "consulted": used,
        "blurb": (
            "Where this tool pulled from — live APIs, SQLite, CSV registers, or the KB index."
        ),
    }

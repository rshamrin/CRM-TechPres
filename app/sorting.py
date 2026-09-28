from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple


SORT_FIELDS: Dict[str, Dict[str, str]] = {
    "status": {"column": "s.order_index", "label": "Статус"},
    "deal_type": {"column": "dt.order_index", "label": "Тип сделки"},
    "last_contact": {"column": "c.last_contact_at", "label": "Последний контакт"},
    "name": {"column": "c.name", "label": "Клиент"},
    "sales": {"column": "c.sales", "label": "Sales"},
    "priority": {"column": "c.priority", "label": "Приоритет"},
}

DEFAULT_SORTS: Sequence[Dict[str, str]] = (
    {"field": "status", "dir": "asc"},
    {"field": "last_contact", "dir": "desc"},
)


def normalize_sorts(sorts: Any) -> List[Dict[str, str]]:
    """Validate browser-provided sorting and fall back to the deals-page default."""
    normalized: List[Dict[str, str]] = []
    seen = set()

    if isinstance(sorts, list):
        for raw in sorts[:5]:
            if not isinstance(raw, dict):
                continue
            field = str(raw.get("field") or "").strip()
            if field not in SORT_FIELDS or field in seen:
                continue
            direction = "desc" if str(raw.get("dir") or "asc").lower() == "desc" else "asc"
            normalized.append({"field": field, "dir": direction})
            seen.add(field)

    if not normalized:
        normalized = [dict(item) for item in DEFAULT_SORTS]
    return normalized


def build_order_clause(sorts: Any) -> Tuple[str, List[Dict[str, str]]]:
    """Build an ORDER BY clause only from allow-listed columns."""
    normalized = normalize_sorts(sorts)
    parts: List[str] = []

    for item in normalized:
        field = item["field"]
        column = SORT_FIELDS[field]["column"]
        direction = "DESC" if item["dir"] == "desc" else "ASC"
        if field == "last_contact":
            # Empty dates always stay at the bottom, exactly as on the deals page.
            parts.append(f"({column} IS NULL) ASC")
        parts.append(f"{column} {direction}")

    # Stable tie-breaker: repeated report builds keep the same row order.
    parts.append("c.id DESC")
    return "ORDER BY " + ", ".join(parts), normalized


def sort_summary(sorts: Any) -> str:
    arrows = {"asc": "↑", "desc": "↓"}
    return " → ".join(
        f"{SORT_FIELDS[item['field']]['label']} {arrows[item['dir']]}"
        for item in normalize_sorts(sorts)
    )

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple


MONTHS_RU = (
    "янв",
    "фев",
    "мар",
    "апр",
    "май",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
)
GRANULARITIES = {"day", "week", "month", "quarter", "year"}
EMPTY_SALES_VALUE = "__EMPTY__"


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normal(value: Any) -> str:
    return _clean_text(value).casefold()


def _event_date(value: Any) -> Optional[date]:
    raw = str(value or "")[:10]
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _date_ru(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _sales_label(value: Any) -> str:
    cleaned = _clean_text(value)
    return cleaned or "Без sales"


def analytics_filter_options(conn) -> Dict[str, List[Dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT e.event_type, COALESCE(c.sales, '') AS sales
        FROM events e
        JOIN clients c ON c.id=e.client_id;
        """
    ).fetchall()

    type_groups: Dict[str, Counter] = {}
    type_totals: Counter = Counter()
    sales_totals: Counter = Counter()

    for row in rows:
        event_type = _clean_text(row["event_type"])
        if event_type:
            key = event_type.casefold()
            type_groups.setdefault(key, Counter())[event_type] += 1
            type_totals[key] += 1
        sales_totals[_clean_text(row["sales"])] += 1

    event_types = []
    for key, total in type_totals.items():
        label = type_groups[key].most_common(1)[0][0]
        event_types.append({"value": label, "label": label, "count": int(total)})
    event_types.sort(key=lambda item: item["label"].casefold())

    sales = [
        {
            "value": raw if raw else EMPTY_SALES_VALUE,
            "label": _sales_label(raw),
            "count": int(total),
        }
        for raw, total in sales_totals.items()
    ]
    sales.sort(key=lambda item: item["label"].casefold())
    return {"event_types": event_types, "sales": sales}


def _matches_dimensions(event: Dict[str, Any], sales: str, event_type: str) -> bool:
    if event_type and _normal(event["event_type"]) != _normal(event_type):
        return False
    if sales:
        raw_sales = _clean_text(event["sales_raw"])
        if sales == EMPTY_SALES_VALUE:
            return raw_sales == ""
        return _normal(raw_sales) == _normal(sales)
    return True


def _matches_comment(text: Any, query: str, match_mode: str) -> bool:
    needle = _normal(query)
    if not needle:
        return True
    haystack = _normal(text)
    if match_mode == "exact":
        return haystack == needle
    return needle in haystack


def _period_start(value: date, granularity: str) -> date:
    if granularity == "day":
        return value
    if granularity == "week":
        return value - timedelta(days=value.weekday())
    if granularity == "month":
        return value.replace(day=1)
    if granularity == "quarter":
        month = ((value.month - 1) // 3) * 3 + 1
        return value.replace(month=month, day=1)
    return value.replace(month=1, day=1)


def _next_period(value: date, granularity: str) -> date:
    if granularity == "day":
        return value + timedelta(days=1)
    if granularity == "week":
        return value + timedelta(days=7)
    if granularity == "month":
        return value.replace(year=value.year + 1, month=1) if value.month == 12 else value.replace(month=value.month + 1)
    if granularity == "quarter":
        month = value.month + 3
        return value.replace(year=value.year + 1, month=month - 12) if month > 12 else value.replace(month=month)
    return value.replace(year=value.year + 1)


def _period_label(start: date, granularity: str) -> str:
    if granularity == "day":
        return _date_ru(start)
    if granularity == "week":
        end = start + timedelta(days=6)
        return f"{start.strftime('%d.%m')}–{end.strftime('%d.%m.%Y')}"
    if granularity == "month":
        return f"{MONTHS_RU[start.month - 1]} {start.year}"
    if granularity == "quarter":
        return f"{((start.month - 1) // 3) + 1} кв. {start.year}"
    return str(start.year)


def _period_rows(date_from: date, date_to: date, granularity: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    cursor = _period_start(date_from, granularity)
    while cursor <= date_to:
        next_cursor = _next_period(cursor, granularity)
        period_end = next_cursor - timedelta(days=1)
        rows.append(
            {
                "key": cursor.isoformat(),
                "label": _period_label(cursor, granularity),
                "date_from": max(cursor, date_from).isoformat(),
                "date_to": min(period_end, date_to).isoformat(),
                "events": 0,
                "client_ids": set(),
                "by_sales": Counter(),
            }
        )
        cursor = next_cursor
    return rows


def _top_comments(events: Iterable[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for event in events:
        text = _clean_text(event["text"])
        if not text:
            continue
        key = text.casefold()
        bucket = groups.setdefault(key, {"count": 0, "variants": Counter()})
        bucket["count"] += 1
        bucket["variants"][text] += 1

    result = [
        {
            "text": bucket["variants"].most_common(1)[0][0],
            "count": int(bucket["count"]),
        }
        for bucket in groups.values()
    ]
    result.sort(key=lambda item: (-item["count"], item["text"].casefold()))
    return result[:limit]


def build_analytics(
    conn,
    date_from: date,
    date_to: date,
    comment: str = "",
    match_mode: str = "contains",
    sales: str = "",
    event_type: str = "",
    granularity: str = "month",
    today: Optional[date] = None,
    detail_limit: int = 5000,
) -> Dict[str, Any]:
    if date_from > date_to:
        raise ValueError("Дата начала не может быть позже даты окончания")
    if (date_to - date_from).days > 3660:
        raise ValueError("Выбери период не более 10 лет")
    if match_mode not in {"contains", "exact"}:
        match_mode = "contains"
    if granularity not in GRANULARITIES:
        granularity = "month"

    today = today or date.today()
    max_plausible_year = today.year + 2
    rows = conn.execute(
        """
        SELECT
            e.id, e.client_id, e.event_at, e.event_type, e.text,
            c.name AS client_name, COALESCE(c.sales, '') AS sales,
            COALESCE(c.is_archived, 0) AS is_archived
        FROM events e
        JOIN clients c ON c.id=e.client_id
        ORDER BY e.id ASC;
        """
    ).fetchall()

    valid_events: List[Dict[str, Any]] = []
    suspicious_dates: List[str] = []
    for row in rows:
        parsed_date = _event_date(row["event_at"])
        if parsed_date is None or parsed_date.year < 2000 or parsed_date.year > max_plausible_year:
            if len(suspicious_dates) < 3:
                suspicious_dates.append(str(row["event_at"] or ""))
            continue
        valid_events.append(
            {
                "id": int(row["id"]),
                "client_id": int(row["client_id"]),
                "event_date": parsed_date,
                "event_type": _clean_text(row["event_type"]),
                "text": _clean_text(row["text"]),
                "client_name": _clean_text(row["client_name"]),
                "sales_raw": _clean_text(row["sales"]),
                "sales": _sales_label(row["sales"]),
                "is_archived": bool(row["is_archived"]),
            }
        )

    dates = [event["event_date"] for event in valid_events]
    data_min = min(dates) if dates else date_from
    data_max = max(dates) if dates else date_to

    span_days = (date_to - date_from).days + 1
    previous_to = date_from - timedelta(days=1)
    previous_from = previous_to - timedelta(days=span_days - 1)

    current_scope = [
        event
        for event in valid_events
        if date_from <= event["event_date"] <= date_to
        and _matches_dimensions(event, sales, event_type)
    ]
    current_matches = [
        event for event in current_scope if _matches_comment(event["text"], comment, match_mode)
    ]
    previous_matches = [
        event
        for event in valid_events
        if previous_from <= event["event_date"] <= previous_to
        and _matches_dimensions(event, sales, event_type)
        and _matches_comment(event["text"], comment, match_mode)
    ]

    period_rows = _period_rows(date_from, date_to, granularity)
    period_map = {row["key"]: row for row in period_rows}
    for event in current_matches:
        key = _period_start(event["event_date"], granularity).isoformat()
        bucket = period_map[key]
        bucket["events"] += 1
        bucket["client_ids"].add(event["client_id"])
        bucket["by_sales"][event["sales"]] += 1

    periods = []
    for row in period_rows:
        periods.append(
            {
                "key": row["key"],
                "label": row["label"],
                "date_from": row["date_from"],
                "date_to": row["date_to"],
                "events": int(row["events"]),
                "unique_clients": len(row["client_ids"]),
                "by_sales": dict(row["by_sales"]),
            }
        )

    sales_stats: Dict[str, Dict[str, Any]] = {}
    type_stats: Dict[str, Dict[str, Any]] = {}
    for event in current_matches:
        sales_bucket = sales_stats.setdefault(
            event["sales"], {"events": 0, "client_ids": set(), "latest": None}
        )
        sales_bucket["events"] += 1
        sales_bucket["client_ids"].add(event["client_id"])
        if sales_bucket["latest"] is None or event["event_date"] > sales_bucket["latest"]:
            sales_bucket["latest"] = event["event_date"]

        type_bucket = type_stats.setdefault(
            event["event_type"] or "Без типа", {"events": 0, "client_ids": set()}
        )
        type_bucket["events"] += 1
        type_bucket["client_ids"].add(event["client_id"])

    total_matches = len(current_matches)
    sales_breakdown = [
        {
            "sales": name,
            "events": int(stats["events"]),
            "unique_clients": len(stats["client_ids"]),
            "share_percent": round(stats["events"] * 100 / total_matches, 1) if total_matches else 0.0,
            "latest_date": stats["latest"].isoformat() if stats["latest"] else None,
        }
        for name, stats in sales_stats.items()
    ]
    sales_breakdown.sort(key=lambda item: (-item["events"], item["sales"].casefold()))

    event_type_breakdown = [
        {
            "event_type": name,
            "events": int(stats["events"]),
            "unique_clients": len(stats["client_ids"]),
        }
        for name, stats in type_stats.items()
    ]
    event_type_breakdown.sort(key=lambda item: (-item["events"], item["event_type"].casefold()))

    previous_count = len(previous_matches)
    if previous_count:
        change_percent: Optional[float] = round((total_matches - previous_count) * 100 / previous_count, 1)
    elif total_matches == 0:
        change_percent = 0.0
    else:
        change_percent = None

    sorted_details = sorted(
        current_matches,
        key=lambda event: (event["event_date"], event["id"]),
        reverse=True,
    )
    details = [
        {
            "id": event["id"],
            "date": event["event_date"].isoformat(),
            "date_ru": _date_ru(event["event_date"]),
            "event_type": event["event_type"],
            "text": event["text"],
            "client_id": event["client_id"],
            "client_name": event["client_name"],
            "sales": event["sales"],
            "is_archived": event["is_archived"],
        }
        for event in sorted_details[:detail_limit]
    ]

    return {
        "meta": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "date_from_ru": _date_ru(date_from),
            "date_to_ru": _date_ru(date_to),
            "comment": _clean_text(comment),
            "match_mode": match_mode,
            "sales": sales,
            "event_type": event_type,
            "granularity": granularity,
            "previous_date_from": previous_from.isoformat(),
            "previous_date_to": previous_to.isoformat(),
            "previous_date_from_ru": _date_ru(previous_from),
            "previous_date_to_ru": _date_ru(previous_to),
        },
        "kpis": {
            "matching_events": total_matches,
            "unique_clients": len({event["client_id"] for event in current_matches}),
            "active_sales": len({event["sales"] for event in current_matches}),
            "scope_events": len(current_scope),
            "share_percent": round(total_matches * 100 / len(current_scope), 1) if current_scope else 0.0,
            "previous_events": previous_count,
            "change_percent": change_percent,
        },
        "periods": periods,
        "sales_breakdown": sales_breakdown,
        "event_type_breakdown": event_type_breakdown,
        "top_comments": _top_comments(current_scope),
        "details": details,
        "details_total": total_matches,
        "details_limited": total_matches > detail_limit,
        "data_bounds": {"date_from": data_min.isoformat(), "date_to": data_max.isoformat()},
        "data_quality": {
            "suspicious_event_dates": len(rows) - len(valid_events),
            "examples": suspicious_dates,
        },
    }

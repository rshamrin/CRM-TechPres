from __future__ import annotations

from datetime import datetime, timedelta, time
import json
import threading
from typing import Any, Dict, List, Tuple

DAYS_AHEAD = 30


def _load_macos_calendar_modules():
    try:
        import objc  # noqa: F401
        from Foundation import NSDate, NSRunLoop
        from EventKit import EKEventStore
        return NSDate, NSRunLoop, EKEventStore
    except ModuleNotFoundError as e:
        missing = getattr(e, "name", "unknown")
        raise RuntimeError(
            "Не установлены зависимости для доступа к календарю macOS. "
            "В активном виртуальном окружении выполни: "
            "pip install pyobjc pyobjc-framework-EventKit"
        ) from e


def py_datetime_to_nsdate(dt: datetime):
    NSDate, _, _ = _load_macos_calendar_modules()
    return NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())


def nsdate_to_py_datetime(nsdate) -> datetime:
    return datetime.fromtimestamp(nsdate.timeIntervalSince1970())


def request_access(store, timeout_seconds: int = 15) -> None:
    NSDate, NSRunLoop, _ = _load_macos_calendar_modules()

    done = threading.Event()
    result = {"granted": False, "error": None}

    def completion(granted, error):
        result["granted"] = bool(granted)
        result["error"] = error
        done.set()

    if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
        store.requestFullAccessToEventsWithCompletion_(completion)
    else:
        store.requestAccessToEntityType_completion_(0, completion)

    end_time = datetime.now() + timedelta(seconds=timeout_seconds)
    while not done.is_set() and datetime.now() < end_time:
        NSRunLoop.currentRunLoop().runUntilDate_(
            NSDate.dateWithTimeIntervalSinceNow_(0.1)
        )

    if not done.is_set():
        raise TimeoutError("Не удалось дождаться ответа на запрос доступа к календарю.")

    if result["error"] is not None:
        raise RuntimeError(f"Ошибка доступа к календарю: {result['error']}")

    if not result["granted"]:
        raise PermissionError("Доступ к календарю не выдан macOS.")


def period_bounds(days_ahead: int = DAYS_AHEAD) -> Tuple[datetime, datetime]:
    now = datetime.now()
    start_dt = datetime(now.year, now.month, now.day, 0, 0, 0)
    end_dt = start_dt + timedelta(days=days_ahead + 1)
    return start_dt, end_dt


def get_events_for_period(start_dt: datetime, end_dt: datetime) -> List[Dict[str, Any]]:
    _, _, EKEventStore = _load_macos_calendar_modules()

    store = EKEventStore.alloc().init()
    request_access(store)

    start_ns = py_datetime_to_nsdate(start_dt)
    end_ns = py_datetime_to_nsdate(end_dt)

    predicate = store.predicateForEventsWithStartDate_endDate_calendars_(
        start_ns,
        end_ns,
        None,
    )

    events = store.eventsMatchingPredicate_(predicate)

    result: List[Dict[str, Any]] = []
    for event in events:
        start_py = nsdate_to_py_datetime(event.startDate())
        end_py = nsdate_to_py_datetime(event.endDate())

        result.append(
            {
                "title": str(event.title()) if event.title() else "Без темы",
                "calendar": str(event.calendar().title()) if event.calendar() else "",
                "location": str(event.location()) if event.location() else "",
                "notes": str(event.notes()) if event.notes() else "",
                "is_all_day": bool(event.isAllDay()),
                "start": start_py.isoformat(),
                "end": end_py.isoformat(),
            }
        )

    result.sort(key=lambda x: (x["start"], x["end"], x["title"]))
    return result


def working_hours_for_day(day: datetime):
    wd = day.weekday()
    if wd in (0, 1, 2, 3):
        return time(9, 0), time(18, 0)
    if wd == 4:
        return time(9, 0), time(17, 0)
    return None


def merge_intervals(intervals):
    if not intervals:
        return []

    intervals = sorted(intervals, key=lambda x: (x[0], x[1]))
    merged = [intervals[0]]

    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            if end > last_end:
                merged[-1] = (last_start, end)
        else:
            merged.append((start, end))

    return merged


def clip_interval(start_a, end_a, start_b, end_b):
    start = max(start_a, start_b)
    end = min(end_a, end_b)
    if start < end:
        return start, end
    return None


def build_rows(events, start_dt: datetime, end_dt: datetime) -> List[Dict[str, Any]]:
    events_by_day: Dict[str, List[Dict[str, Any]]] = {}

    for ev in events:
        ev_start = datetime.fromisoformat(ev["start"])
        ev_end = datetime.fromisoformat(ev["end"])

        day_cursor = datetime(ev_start.year, ev_start.month, ev_start.day)
        last_day = datetime(ev_end.year, ev_end.month, ev_end.day)

        while day_cursor <= last_day:
            day_key = day_cursor.date().isoformat()
            events_by_day.setdefault(day_key, []).append(ev)
            day_cursor += timedelta(days=1)

    rows: List[Dict[str, Any]] = []
    day = start_dt
    today_str = datetime.now().date().isoformat()
    ru_days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    while day < end_dt:
        day_key = day.date().isoformat()
        wh = working_hours_for_day(day)

        if wh is None:
            day += timedelta(days=1)
            continue

        ws, we = wh
        work_start = datetime.combine(day.date(), ws)
        work_end = datetime.combine(day.date(), we)

        busy_intervals = []
        day_events: List[Dict[str, Any]] = []

        for ev in events_by_day.get(day_key, []):
            ev_start = datetime.fromisoformat(ev["start"])
            ev_end = datetime.fromisoformat(ev["end"])

            if ev["is_all_day"]:
                clipped = (work_start, work_end)
                time_label = "Весь день"
            else:
                clipped = clip_interval(ev_start, ev_end, work_start, work_end)
                time_label = None

            if clipped:
                busy_intervals.append(clipped)

                if not time_label:
                    time_label = f"{clipped[0].strftime('%H:%M')}-{clipped[1].strftime('%H:%M')}"

                day_events.append(
                    {
                        "date": day_key,
                        "day_name": ru_days[day.weekday()],
                        "is_today": day_key == today_str,
                        "kind": "busy",
                        "start_sort": clipped[0].strftime('%H:%M'),
                        "time": time_label,
                        "title": ev["title"],
                        "calendar": ev["calendar"],
                        "location": ev["location"],
                    }
                )

        busy_intervals = merge_intervals(busy_intervals)

        day_free: List[Dict[str, Any]] = []
        cursor = work_start

        for b_start, b_end in busy_intervals:
            if cursor < b_start:
                day_free.append(
                    {
                        "date": day_key,
                        "day_name": ru_days[day.weekday()],
                        "is_today": day_key == today_str,
                        "kind": "free",
                        "start_sort": cursor.strftime('%H:%M'),
                        "time": f"{cursor.strftime('%H:%M')}-{b_start.strftime('%H:%M')}",
                        "title": "Свободно",
                        "calendar": "",
                        "location": "",
                    }
                )
            if cursor < b_end:
                cursor = b_end

        if cursor < work_end:
            day_free.append(
                {
                    "date": day_key,
                    "day_name": ru_days[day.weekday()],
                    "is_today": day_key == today_str,
                    "kind": "free",
                    "start_sort": cursor.strftime('%H:%M'),
                    "time": f"{cursor.strftime('%H:%M')}-{work_end.strftime('%H:%M')}",
                    "title": "Свободно",
                    "calendar": "",
                    "location": "",
                }
            )

        day_events.sort(key=lambda x: x["start_sort"])
        day_free.sort(key=lambda x: x["start_sort"])

        rows.extend(day_events)
        rows.extend(day_free)
        day += timedelta(days=1)

    rows.sort(key=lambda x: (x["date"], x["start_sort"], 0 if x["kind"] == "busy" else 1))
    return rows


def build_html(rows, start_dt: datetime, end_dt: datetime) -> str:
    data_json = (
        json.dumps(rows, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("'", "\\u0027")
        .replace("</", "<\\/")
    )

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Календарь на 30 дней</title>
<style>
    :root {{
        --bg: #ffffff;
        --fg: #111111;
        --muted: #6b7280;
        --line: #e5e7eb;
        --line-strong: #d1d5db;
        --busy-bg: #f8fafc;
        --free-bg: #f0fdf4;
        --free-fg: #166534;
        --today-bg: #fff7ed;
        --today-border: #fb923c;
        --chip-bg: #f3f4f6;
        --date-bg: #fcfcfd;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; font-size: 12px; line-height: 1.15; color: var(--fg); background: var(--bg); }}
    .wrap {{ max-width: 1600px; margin: 0 auto; padding: 8px 10px 18px; }}
    .toolbar {{ position: sticky; top: 0; z-index: 10; display: flex; gap: 8px; align-items: center; justify-content: space-between; padding: 6px 0 8px; background: var(--bg); border-bottom: 1px solid var(--line); margin-bottom: 6px; }}
    .title-wrap {{ display: flex; align-items: baseline; min-width: 0; gap: 8px; flex-wrap: wrap; }}
    .title {{ font-size: 13px; font-weight: 700; white-space: nowrap; }}
    .meta {{ color: var(--muted); font-size: 11px; }}
    .controls {{ display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }}
    .btn-link {{ text-decoration: none; border: 1px solid var(--line-strong); background: #fff; color: var(--fg); border-radius: 8px; padding: 4px 8px; font-size: 11px; line-height: 1; cursor: pointer; }}
    button {{ border: 1px solid var(--line-strong); background: #fff; color: var(--fg); border-radius: 8px; padding: 4px 8px; font-size: 11px; line-height: 1; cursor: pointer; }}
    button.active {{ background: #111827; color: #fff; border-color: #111827; }}
    .hint {{ color: var(--muted); font-size: 11px; }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    thead th {{ position: sticky; top: 37px; z-index: 5; background: #fff; font-size: 10px; text-transform: uppercase; letter-spacing: .03em; color: var(--muted); text-align: left; padding: 4px 6px; border-bottom: 1px solid var(--line-strong); white-space: nowrap; }}
    tbody td {{ padding: 3px 6px; border-bottom: 1px solid var(--line); vertical-align: middle; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    tbody tr.busy {{ background: var(--busy-bg); }}
    tbody tr.free {{ background: var(--free-bg); }}
    tbody tr.today td:not(.date-cell) {{ background: var(--today-bg); }}
    tbody tr.day-start td {{ border-top: 1px solid var(--line-strong); }}
    .col-dategroup {{ width: 72px; }} .col-time {{ width: 86px; }} .col-kind {{ width: 74px; }} .col-cal {{ width: 130px; }} .col-loc {{ width: 150px; }}
    .date-cell {{ vertical-align: top; padding: 4px 6px 3px; border-right: 1px solid var(--line); background: var(--date-bg); white-space: normal; overflow: visible; text-overflow: clip; }}
    .today .date-cell {{ background: #fff3e8; }}
    .date-main {{ font-size: 12px; font-weight: 700; line-height: 1.05; }}
    .date-sub {{ margin-top: 2px; font-size: 10px; color: var(--muted); line-height: 1.05; }}
    .date-today {{ margin-top: 2px; font-size: 9px; color: #c2410c; line-height: 1.05; font-weight: 700; }}
    .kind-chip {{ display: inline-block; padding: 1px 6px; border-radius: 999px; font-size: 10px; line-height: 1.2; background: var(--chip-bg); }}
    .kind-busy {{ background: #e5e7eb; color: #111827; }}
    .kind-free {{ background: #dcfce7; color: var(--free-fg); }}
    .empty {{ padding: 14px 6px; color: var(--muted); }}
</style>
</head>
<body>
<div class="wrap">
    <div class="toolbar">
        <div class="title-wrap">
            <div class="title">Календарь встреч</div>
            <div class="meta">{start_dt.strftime('%d.%m.%Y')} — {(end_dt - timedelta(days=1)).strftime('%d.%m.%Y')}</div>
        </div>
        <div class="controls">
            <a class="btn-link" href="/">Сделки</a>
            <a class="btn-link" href="/analytics">BI‑аналитика</a>
            <button id="btn-all" class="active">Все интервалы</button>
            <button id="btn-free">Только свободные</button>
            <span class="hint">Пн–Чт 09:00–18:00, Пт 09:00–17:00</span>
        </div>
    </div>

    <table>
        <thead>
            <tr>
                <th class="col-dategroup">Дата</th>
                <th class="col-time">Время</th>
                <th class="col-kind">Тип</th>
                <th>Тема</th>
                <th class="col-cal">Календарь</th>
                <th class="col-loc">Место</th>
            </tr>
        </thead>
        <tbody id="tbody"></tbody>
    </table>
</div>

<script>
const rows = {data_json};
const tbody = document.getElementById('tbody');
const btnAll = document.getElementById('btn-all');
const btnFree = document.getElementById('btn-free');
let mode = 'all';
function escapeHtml(value) {{
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}}
function groupRowsByDate(items) {{
    const groups = [];
    let current = null;
    for (const row of items) {{
        if (!current || current.date !== row.date) {{
            current = {{ date: row.date, day_name: row.day_name, is_today: row.is_today, items: [] }};
            groups.push(current);
        }}
        current.items.push(row);
    }}
    return groups;
}}
function render() {{
    const filtered = mode === 'free' ? rows.filter(r => r.kind === 'free') : rows;
    if (!filtered.length) {{
        tbody.innerHTML = '<tr><td colspan="6" class="empty">Нет строк для отображения</td></tr>';
        return;
    }}
    const groups = groupRowsByDate(filtered);
    tbody.innerHTML = groups.map(group => {{
        const dateLabel = group.date.slice(8, 10) + '.' + group.date.slice(5, 7);
        return group.items.map((r, index) => {{
            const kindLabel = r.kind === 'free'
                ? '<span class="kind-chip kind-free">Свободно</span>'
                : '<span class="kind-chip kind-busy">Встреча</span>';
            const dateCell = index === 0
                ? `<td class="col-dategroup date-cell" rowspan="${{group.items.length}}"><div class="date-main">${{escapeHtml(dateLabel)}}</div><div class="date-sub">${{escapeHtml(group.day_name)}}</div>${{group.is_today ? '<div class="date-today">сегодня</div>' : ''}}</td>`
                : '';
            return `<tr class="${{escapeHtml(r.kind)}}${{r.is_today ? ' today' : ''}}${{index === 0 ? ' day-start' : ''}}">${{dateCell}}<td class="col-time">${{escapeHtml(r.time)}}</td><td class="col-kind">${{kindLabel}}</td><td title="${{escapeHtml(r.title)}}">${{escapeHtml(r.title)}}</td><td class="col-cal" title="${{escapeHtml(r.calendar)}}">${{escapeHtml(r.calendar)}}</td><td class="col-loc" title="${{escapeHtml(r.location)}}">${{escapeHtml(r.location)}}</td></tr>`;
        }}).join('');
    }}).join('');
}}
btnAll.addEventListener('click', () => {{ mode = 'all'; btnAll.classList.add('active'); btnFree.classList.remove('active'); render(); }});
btnFree.addEventListener('click', () => {{ mode = 'free'; btnFree.classList.add('active'); btnAll.classList.remove('active'); render(); }});
render();
</script>
</body>
</html>"""


def build_calendar_page(days_ahead: int = DAYS_AHEAD) -> str:
    start_dt, end_dt = period_bounds(days_ahead)
    events = get_events_for_period(start_dt, end_dt)
    rows = build_rows(events, start_dt, end_dt)
    return build_html(rows, start_dt, end_dt)

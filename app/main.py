from __future__ import annotations












import csv
import inspect
import json
import sqlite3
import uuid
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .analytics import analytics_filter_options, build_analytics
from .clearmail import build_calendar_page
from .db import (
    connect,
    get_all_deal_types,
    get_all_sales,
    get_all_statuses,
    get_deal_types,
    get_sales,
    get_statuses,
    init_db,
    update_last_contact,
    utcnow_iso,
)
from .sorting import build_order_clause, sort_summary

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"
BACKUP_DIR = BASE_DIR / "backups"
ATTACHMENTS_DIR = DATA_DIR / "attachments"
MAX_ATTACHMENT_FILE_BYTES = 100 * 1024 * 1024
MAX_ATTACHMENT_BATCH_BYTES = 500 * 1024 * 1024
MAX_ATTACHMENT_BATCH_FILES = 500

app = FastAPI(title="Mini-CRM", version="0.5")

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")

_TEMPLATE_RESPONSE_HAS_REQUEST_ARG = "request" in inspect.signature(templates.TemplateResponse).parameters


def _template_response(request: Request, name: str, context: Dict[str, Any]):
    full_context = {"request": request, **context}
    if _TEMPLATE_RESPONSE_HAS_REQUEST_ARG:
        return templates.TemplateResponse(request=request, name=name, context=full_context)
    return templates.TemplateResponse(name, full_context)


def _parse_iso(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None


def _clean_attachment_path(filename: str) -> str:
    """Keep a browser-supplied folder path, but never trust it as a disk path."""
    raw = (filename or "").replace("\\", "/").replace("\x00", "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="У файла отсутствует имя")

    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise HTTPException(status_code=400, detail="Некорректный путь файла")

    parts = []
    for part in path.parts:
        cleaned = part.strip()
        if not cleaned or cleaned in {".", ".."}:
            raise HTTPException(status_code=400, detail="Некорректный путь файла")
        if len(cleaned) > 255:
            raise HTTPException(status_code=400, detail="Слишком длинное имя файла или папки")
        parts.append(cleaned)

    relative_path = "/".join(parts)
    if len(relative_path) > 1000:
        raise HTTPException(status_code=400, detail="Слишком длинный путь файла")
    return relative_path


def _attachment_disk_path(client_id: int, stored_name: str) -> Path:
    safe_name = Path(stored_name or "").name
    if not safe_name or safe_name != stored_name:
        raise HTTPException(status_code=500, detail="Некорректная запись вложения")
    return ATTACHMENTS_DIR / str(int(client_id)) / safe_name


def _decode_attachment_paths(value: str) -> List[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Некорректный список путей вложений") from exc
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise HTTPException(status_code=400, detail="Некорректный список путей вложений")
    return parsed


def _attachment_to_dict(row: Any) -> Dict[str, Any]:
    data = dict(row)
    data["id"] = int(data["id"])
    data["client_id"] = int(data["client_id"])
    data["event_id"] = int(data["event_id"]) if data.get("event_id") is not None else None
    data["size_bytes"] = int(data.get("size_bytes") or 0)
    data.pop("stored_name", None)
    data["download_url"] = f"/api/attachments/{data['id']}/download"
    return data


def _get_client_attachments(conn: Any, client_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.id, a.client_id, a.event_id, a.original_name, a.relative_path,
               a.stored_name, a.mime_type, a.size_bytes, a.created_at,
               e.event_type, e.event_at
        FROM attachments a
        LEFT JOIN events e ON e.id=a.event_id
        WHERE a.client_id=?
        ORDER BY a.created_at DESC, a.id DESC;
        """,
        (client_id,),
    ).fetchall()
    return [_attachment_to_dict(row) for row in rows]


def _unlink_attachment_rows(rows: List[Any]) -> None:
    for row in rows:
        try:
            path = _attachment_disk_path(int(row["client_id"]), str(row["stored_name"]))
            path.unlink(missing_ok=True)
        except (OSError, TypeError, ValueError, HTTPException):
            # A missing/orphaned file must not block deleting the database record.
            pass


async def _save_uploaded_attachments(
    client_id: int,
    event_id: Optional[int],
    files: List[UploadFile],
    relative_paths: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    if not files:
        raise HTTPException(status_code=400, detail="Не выбраны файлы")
    if len(files) > MAX_ATTACHMENT_BATCH_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"За один раз можно загрузить не более {MAX_ATTACHMENT_BATCH_FILES} файлов",
        )

    client_dir = ATTACHMENTS_DIR / str(int(client_id))
    client_dir.mkdir(parents=True, exist_ok=True)
    created_paths: List[Path] = []
    created_ids: List[int] = []
    batch_size = 0

    with connect(DB_PATH) as conn:
        client = conn.execute("SELECT id FROM clients WHERE id=?;", (client_id,)).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Клиент не найден")

        if event_id is not None:
            event = conn.execute(
                "SELECT id FROM events WHERE id=? AND client_id=?;",
                (event_id, client_id),
            ).fetchone()
            if not event:
                raise HTTPException(status_code=404, detail="Событие не найдено у этого клиента")

        try:
            for index, uploaded in enumerate(files):
                supplied_path = ""
                if relative_paths and index < len(relative_paths):
                    supplied_path = relative_paths[index]
                relative_path = _clean_attachment_path(supplied_path or uploaded.filename or "")
                original_name = PurePosixPath(relative_path).name
                stored_name = uuid.uuid4().hex
                destination = client_dir / stored_name
                created_paths.append(destination)
                size = 0

                try:
                    with destination.open("xb") as output:
                        while True:
                            chunk = await uploaded.read(1024 * 1024)
                            if not chunk:
                                break
                            size += len(chunk)
                            batch_size += len(chunk)
                            if size > MAX_ATTACHMENT_FILE_BYTES:
                                raise HTTPException(
                                    status_code=413,
                                    detail=f"Файл «{original_name}» больше 100 МБ",
                                )
                            if batch_size > MAX_ATTACHMENT_BATCH_BYTES:
                                raise HTTPException(
                                    status_code=413,
                                    detail="Общий размер одной загрузки не должен превышать 500 МБ",
                                )
                            output.write(chunk)
                finally:
                    await uploaded.close()

                cur = conn.execute(
                    """
                    INSERT INTO attachments(
                        client_id, event_id, original_name, relative_path,
                        stored_name, mime_type, size_bytes, created_at
                    ) VALUES(?,?,?,?,?,?,?,?);
                    """,
                    (
                        client_id,
                        event_id,
                        original_name,
                        relative_path,
                        stored_name,
                        (uploaded.content_type or "application/octet-stream")[:255],
                        size,
                        utcnow_iso(),
                    ),
                )
                created_ids.append(int(cur.lastrowid))

            conn.commit()
            placeholders = ",".join("?" for _ in created_ids)
            rows = conn.execute(
                f"""
                SELECT a.id, a.client_id, a.event_id, a.original_name, a.relative_path,
                       a.stored_name, a.mime_type, a.size_bytes, a.created_at,
                       e.event_type, e.event_at
                FROM attachments a
                LEFT JOIN events e ON e.id=a.event_id
                WHERE a.id IN ({placeholders})
                ORDER BY a.id ASC;
                """,
                created_ids,
            ).fetchall()
            return [_attachment_to_dict(row) for row in rows]
        except HTTPException:
            conn.rollback()
            for path in created_paths:
                path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            conn.rollback()
            for path in created_paths:
                path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="Не удалось сохранить вложения") from exc


def _csv_stream(rows: List[List[Any]], header: List[str]):
    def gen():
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(header)
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        for r in rows:
            writer.writerow(r)
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    return gen()


@app.on_event("startup")
def _startup():
    init_db(DB_PATH)


# ----------------------------
# Pages
# ----------------------------
@app.get("/", response_class=HTMLResponse)
def page_deals(request: Request):
    with connect(DB_PATH) as conn:
        statuses = [dict(r) for r in get_statuses(conn)]
        deal_types = [dict(r) for r in get_deal_types(conn)]
        sales = [dict(r) for r in get_sales(conn)]
    return _template_response(
        request,
        "deals.html",
        {"statuses": statuses, "deal_types": deal_types, "sales": sales},
    )


@app.get("/activity", response_class=HTMLResponse)
def page_activity(request: Request):
    with connect(DB_PATH) as conn:
        statuses = [dict(r) for r in get_statuses(conn)]
        deal_types = [dict(r) for r in get_deal_types(conn)]
        sales = [dict(r) for r in get_sales(conn)]
    return _template_response(
        request,
        "activity.html",
        {"statuses": statuses, "deal_types": deal_types, "sales": sales},
    )


@app.get("/report", response_class=HTMLResponse)
def page_report(request: Request):
    # Report page: deals with activity in last N days, grouped by deal type
    with connect(DB_PATH) as conn:
        deal_types = [dict(r) for r in get_deal_types(conn)]
        sales = [dict(r) for r in get_sales(conn)]
        statuses = [dict(r) for r in get_statuses(conn)]
    return _template_response(
        request,
        "report.html",
        {"deal_types": deal_types, "sales": sales, "statuses": statuses},
    )


@app.get("/analytics", response_class=HTMLResponse)
def page_analytics(request: Request):
    today = datetime.utcnow().date()
    with connect(DB_PATH) as conn:
        options = analytics_filter_options(conn)
    return _template_response(
        request,
        "analytics.html",
        {
            "default_from": today.replace(month=1, day=1).isoformat(),
            "default_to": today.isoformat(),
            "event_types": options["event_types"],
            "sales_options": options["sales"],
        },
    )


@app.get("/clearmail", response_class=HTMLResponse)
def page_clearmail():
    try:
        html = build_calendar_page()
        return HTMLResponse(content=html)
    except Exception as e:
        message = str(e)
        fallback_html = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Календарь</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;padding:24px;background:#f8fafc;color:#111827}} .card{{max-width:860px;margin:0 auto;background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:24px}} code,pre{{background:#f3f4f6;border-radius:8px;padding:2px 6px}} pre{{padding:12px;overflow:auto;white-space:pre-wrap}} a{{color:#2563eb;text-decoration:none}} .muted{{color:#6b7280}}</style></head>
<body><div class="card"><h1>Календарь пока не открылся</h1><p>CRM работает, но модуль календаря macOS не смог загрузиться.</p><p><strong>Что сделать:</strong></p><pre>source venv/bin/activate
pip install -r requirements.txt</pre><p>Если не поможет, отдельно выполни:</p><pre>pip install pyobjc pyobjc-framework-EventKit</pre><p class="muted">Текст ошибки: {message}</p><p><a href="/">← Вернуться в CRM</a></p></div></body></html>"""
        return HTMLResponse(content=fallback_html, status_code=500)


def _iso_date(s: str) -> str:
    # event_at may be ISO datetime; compare by date part only
    return (s or "")[:10]


def _build_report(days: int, sorts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    days = int(days or 14)
    if days < 1:
        days = 1
    if days > 365:
        days = 365

    today = datetime.utcnow().date()
    cutoff = today - timedelta(days=days - 1)
    cutoff_str = cutoff.isoformat()

    # highlight dates within last 14 days
    highlight_cutoff = today - timedelta(days=14)
    highlight_str = highlight_cutoff.isoformat()
    order_sql, normalized_sorts = build_order_clause(sorts)

    with connect(DB_PATH) as conn:
        dtypes = [dict(r) for r in get_deal_types(conn)]
        rows = conn.execute(
            f"""
            SELECT
              c.id, c.name, c.sales,
              s.name AS status_name, s.order_index AS status_order, s.is_final AS status_final,
              dt.id AS deal_type_id, dt.name AS deal_type_name, dt.order_index AS deal_type_order,
              c.last_contact_at
            FROM clients c
            LEFT JOIN statuses s ON s.id = c.status_id
            LEFT JOIN deal_types dt ON dt.id = c.deal_type_id
            WHERE c.is_archived=0
              AND EXISTS (
                SELECT 1 FROM events e
                WHERE e.client_id = c.id
                  AND substr(e.event_at,1,10) >= ?
              )
            {order_sql};
            """,
            (cutoff_str,),
        ).fetchall()

        clients: List[Dict[str, Any]] = []
        for r in rows:
            cid = int(r["id"])
            evs = conn.execute(
                """
                SELECT event_type, event_at, text
                FROM events
                WHERE client_id=?
                ORDER BY event_at DESC, id DESC
                LIMIT 5;
                """,
                (cid,),
            ).fetchall()

            events_fmt: List[Dict[str, Any]] = []
            for e in evs:
                d = _iso_date(e["event_at"])
                try:
                    y, m, dd = d.split("-")
                    date_ru = f"{dd}.{m}.{y}"
                except Exception:
                    date_ru = d
                events_fmt.append(
                    {
                        "date_iso": d,
                        "date_ru": date_ru,
                        "recent": bool(d >= highlight_str),
                        "type": (e["event_type"] or "").strip(),
                        "text": (e["text"] or "").strip(),
                    }
                )

            clients.append(
                {
                    "id": cid,
                    "name": (r["name"] or "").strip(),
                    "sales": (r["sales"] or "").strip(),
                    "status": (r["status_name"] or "").strip(),
                    "status_final": int(r["status_final"] or 0),
                    "deal_type_id": int(r["deal_type_id"]) if r["deal_type_id"] is not None else None,
                    "deal_type": (r["deal_type_name"] or "Без типа").strip(),
                    "events": events_fmt,
                }
            )

    # group by deal type keeping deal_types order
    groups: List[Dict[str, Any]] = []
    order_names = [str(d["name"]) for d in dtypes]
    buckets: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for c in clients:
        key = c["deal_type"] if c["deal_type_id"] is not None else "Без типа"
        b = buckets.setdefault(key, {"active": [], "final": []})
        if int(c.get("status_final") or 0) == 1:
            b["final"].append(c)
        else:
            b["active"].append(c)

    for name in order_names:
        if name in buckets:
            b = buckets[name]
            groups.append({"name": name, "active": b["active"], "final": b["final"]})
    if "Без типа" in buckets:
        b = buckets["Без типа"]
        groups.append({"name": "Без типа", "active": b["active"], "final": b["final"]})

    return {
        "days": days,
        "date_from": cutoff.strftime("%d.%m.%Y"),
        "date_to": today.strftime("%d.%m.%Y"),
        "sorts": normalized_sorts,
        "sort_summary": sort_summary(normalized_sorts),
        "groups": groups,
    }


@app.get("/api/report")
def api_report(days: int = 14, sorts: str = ""):
    try:
        parsed_sorts = json.loads(sorts) if sorts else []
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed_sorts = []
    data = _build_report(days, parsed_sorts if isinstance(parsed_sorts, list) else [])
    html = templates.env.get_template("report_email.html").render(**data)
    return {
        "ok": True,
        "meta": {
            "days": data["days"],
            "date_from": data["date_from"],
            "date_to": data["date_to"],
            "sort_summary": data["sort_summary"],
        },
        "html": html,
    }


def _analytics_date(value: str, fallback: date, field_name: str) -> date:
    if not value:
        return fallback
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Некорректная дата в поле «{field_name}»") from exc


@app.get("/api/analytics")
def api_analytics(
    date_from: str = "",
    date_to: str = "",
    comment: str = "",
    match_mode: str = "contains",
    sales: str = "",
    event_type: str = "",
    granularity: str = "month",
):
    today = datetime.utcnow().date()
    parsed_from = _analytics_date(date_from, today.replace(month=1, day=1), "Дата с")
    parsed_to = _analytics_date(date_to, today, "Дата по")
    try:
        with connect(DB_PATH) as conn:
            return build_analytics(
                conn,
                date_from=parsed_from,
                date_to=parsed_to,
                comment=comment,
                match_mode=match_mode,
                sales=sales,
                event_type=event_type,
                granularity=granularity,
                today=today,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

# ----------------------------
# Statuses CRUD (hard delete)
# ----------------------------
@app.get("/api/statuses")
def api_statuses(all: int = 0):
    with connect(DB_PATH) as conn:
        rows = get_all_statuses(conn) if all else get_statuses(conn)
    return [dict(r) for r in rows]


@app.post("/api/statuses")
async def api_statuses_create(payload: Dict[str, Any]):
    name = (payload.get("name") or "").strip()
    order_index = int(payload.get("order_index") or 100)
    is_final = int(bool(payload.get("is_final") or 0))

    if not name:
        raise HTTPException(status_code=400, detail="Название статуса обязательно")

    with connect(DB_PATH) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO statuses(name, order_index, is_active, is_final) VALUES(?,?,1,?);",
                (name, order_index, is_final),
            )
            conn.commit()
            return {"id": cur.lastrowid}
        except Exception as e:
            raise HTTPException(status_code=400, detail="Не удалось создать статус (возможно, уже существует)") from e


@app.put("/api/statuses/{status_id}")
async def api_statuses_update(status_id: int, payload: Dict[str, Any]):
    name = (payload.get("name") or "").strip()
    order_index = int(payload.get("order_index") or 100)
    is_final = int(bool(payload.get("is_final") or 0))

    if not name:
        raise HTTPException(status_code=400, detail="Название статуса обязательно")

    with connect(DB_PATH) as conn:
        try:
            conn.execute(
                "UPDATE statuses SET name=?, order_index=?, is_final=? WHERE id=?;",
                (name, order_index, is_final, status_id),
            )
            conn.commit()
        except Exception as e:
            raise HTTPException(status_code=400, detail="Не удалось обновить статус") from e
    return {"ok": True}


@app.delete("/api/statuses/{status_id}")
def api_statuses_delete(status_id: int):
    with connect(DB_PATH) as conn:
        used = conn.execute("SELECT COUNT(*) AS c FROM clients WHERE status_id=?;", (status_id,)).fetchone()["c"]
        if used and int(used) > 0:
            raise HTTPException(status_code=400, detail="Нельзя удалить: статус используется клиентами")
        conn.execute("DELETE FROM statuses WHERE id=?;", (status_id,))
        conn.commit()
    return {"ok": True}


# ----------------------------
# Deal types CRUD (hard delete)
# ----------------------------
@app.get("/api/deal-types")
def api_deal_types(all: int = 0):
    with connect(DB_PATH) as conn:
        rows = get_all_deal_types(conn) if all else get_deal_types(conn)
    return [dict(r) for r in rows]


@app.post("/api/deal-types")
async def api_deal_types_create(payload: Dict[str, Any]):
    name = (payload.get("name") or "").strip()
    order_index = int(payload.get("order_index") or 100)

    if not name:
        raise HTTPException(status_code=400, detail="Название типа сделки обязательно")

    with connect(DB_PATH) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO deal_types(name, order_index, is_active) VALUES(?,?,1);",
                (name, order_index),
            )
            conn.commit()
            return {"id": cur.lastrowid}
        except Exception as e:
            raise HTTPException(status_code=400, detail="Не удалось создать тип (возможно, уже существует)") from e


@app.put("/api/deal-types/{deal_type_id}")
async def api_deal_types_update(deal_type_id: int, payload: Dict[str, Any]):
    name = (payload.get("name") or "").strip()
    order_index = int(payload.get("order_index") or 100)
    if not name:
        raise HTTPException(status_code=400, detail="Название типа сделки обязательно")

    with connect(DB_PATH) as conn:
        try:
            conn.execute(
                "UPDATE deal_types SET name=?, order_index=? WHERE id=?;",
                (name, order_index, deal_type_id),
            )
            conn.commit()
        except Exception as e:
            raise HTTPException(status_code=400, detail="Не удалось обновить тип сделки") from e
    return {"ok": True}


@app.delete("/api/deal-types/{deal_type_id}")
def api_deal_types_delete(deal_type_id: int):
    with connect(DB_PATH) as conn:
        used = conn.execute("SELECT COUNT(*) AS c FROM clients WHERE deal_type_id=?;", (deal_type_id,)).fetchone()["c"]
        if used and int(used) > 0:
            raise HTTPException(status_code=400, detail="Нельзя удалить: тип сделки используется клиентами")
        conn.execute("DELETE FROM deal_types WHERE id=?;", (deal_type_id,))
        conn.commit()
    return {"ok": True}



# ----------------------------
# Sales CRUD (hard delete)
# ----------------------------
@app.get("/api/sales")
def api_sales(all: int = 0):
    with connect(DB_PATH) as conn:
        rows = get_all_sales(conn) if all else get_sales(conn)
    return [dict(r) for r in rows]


@app.post("/api/sales")
async def api_sales_create(payload: Dict[str, Any]):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Имя sales обязательно")

    with connect(DB_PATH) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO sales_people(name, is_active) VALUES(?,1);",
                (name,),
            )
            conn.commit()
            return {"id": cur.lastrowid}
        except Exception as e:
            raise HTTPException(status_code=400, detail="Не удалось создать sales (возможно, уже существует)") from e


@app.put("/api/sales/{sales_id}")
async def api_sales_update(sales_id: int, payload: Dict[str, Any]):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Имя sales обязательно")

    with connect(DB_PATH) as conn:
        row = conn.execute("SELECT name FROM sales_people WHERE id=?;", (sales_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Не найдено")
        old = (row["name"] or "").strip()

        try:
            conn.execute("UPDATE sales_people SET name=? WHERE id=?;", (name, sales_id))
            # keep clients in sync
            if old and old != name:
                conn.execute("UPDATE clients SET sales=? WHERE TRIM(COALESCE(sales,''))=?;", (name, old))
            conn.commit()
        except Exception as e:
            raise HTTPException(status_code=400, detail="Не удалось обновить sales (возможно, имя уже занято)") from e
    return {"ok": True}


@app.delete("/api/sales/{sales_id}")
def api_sales_delete(sales_id: int):
    with connect(DB_PATH) as conn:
        row = conn.execute("SELECT name FROM sales_people WHERE id=?;", (sales_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Не найдено")
        name = (row["name"] or "").strip()

        used = conn.execute(
            "SELECT COUNT(*) AS c FROM clients WHERE TRIM(COALESCE(sales,''))=?;",
            (name,),
        ).fetchone()["c"]
        if used and int(used) > 0:
            raise HTTPException(status_code=400, detail="Нельзя удалить: sales используется клиентами")

        conn.execute("DELETE FROM sales_people WHERE id=?;", (sales_id,))
        conn.commit()
    return {"ok": True}


# ----------------------------
# Clients & Events
# ----------------------------

def _timeline_last_n(conn, client_id: int, limit: int = 5) -> Dict[str, Any]:
    """Return last N events (newest first) + total count for a client."""
    rows = conn.execute(
        """
        SELECT id, event_at, event_type, text
        FROM events
        WHERE client_id=?
        ORDER BY event_at DESC, id DESC
        LIMIT ?;
        """,
        (client_id, limit),
    ).fetchall()

    total = conn.execute(
        "SELECT COUNT(*) AS c FROM events WHERE client_id=?;",
        (client_id,),
    ).fetchone()["c"]

    items: List[Dict[str, Any]] = []
    for r in rows:
        txt = (r["text"] or "").strip().replace("\n", " ")
        if len(txt) > 160:
            txt = txt[:157] + "…"
        items.append(
            {
                "id": int(r["id"]),
                "event_at": r["event_at"],
                "event_type": r["event_type"],
                "text": txt,
            }
        )

    more = int(total or 0) - len(items)
    if more < 0:
        more = 0
    return {"items": items, "more": more, "total": int(total or 0)}


def _apply_filters_to_query(where: List[str], params: List[Any], filters: List[Dict[str, Any]]):
    # filters: list of {field, op, value} AND-combined
    for f in filters:
        field = (f.get("field") or "").strip()
        op = (f.get("op") or "").strip()
        val = f.get("value")

        if field == "name":
            if op == "contains" and val:
                where.append("c.name LIKE ?")
                params.append(f"%{val}%")
            elif op == "equals" and val:
                where.append("c.name = ?")
                params.append(val)

        elif field == "sales":
            if op == "contains" and val:
                where.append("COALESCE(c.sales,'') LIKE ?")
                params.append(f"%{val}%")
            elif op == "equals" and val:
                where.append("COALESCE(c.sales,'') = ?")
                params.append(val)

        elif field == "status_id":
            ids = [int(x) for x in (val or []) if str(x).isdigit()]
            if ids:
                where.append(f"c.status_id IN ({','.join(['?']*len(ids))})")
                params.extend(ids)

        elif field == "deal_type_id":
            ids = [int(x) for x in (val or []) if str(x).isdigit()]
            if ids:
                where.append(f"c.deal_type_id IN ({','.join(['?']*len(ids))})")
                params.extend(ids)

        elif field == "priority":
            try:
                v = int(val)
            except Exception:
                continue
            if op == ">=":
                where.append("c.priority >= ?")
                params.append(v)
            elif op == "<=":
                where.append("c.priority <= ?")
                params.append(v)
            elif op == "=":
                where.append("c.priority = ?")
                params.append(v)

        elif field == "last_contact_days":
            try:
                v = int(val)
            except Exception:
                continue
            expr = "(CAST((julianday('now') - julianday(c.last_contact_at)) AS INTEGER))"
            if op == ">=":
                where.append(f"({expr} >= ? OR c.last_contact_at IS NULL)")
                params.append(v)
            elif op == "<=":
                where.append(f"{expr} <= ?")
                params.append(v)

        elif field == "event_text":
            if op == "contains" and val:
                where.append("EXISTS (SELECT 1 FROM events e WHERE e.client_id=c.id AND e.text LIKE ?)")
                params.append(f"%{val}%")

    return where, params


@app.post("/api/clients/query")
async def api_clients_query(payload: Dict[str, Any]):
    status_ids = payload.get("status_ids") or []
    deal_type_ids = payload.get("deal_type_ids") or []
    q = (payload.get("q") or "").strip()
    q_cf = q.casefold()
    include_archived = bool(payload.get("include_archived") or False)

    filters = payload.get("filters") or []
    sorts = payload.get("sorts") or []

    where: List[str] = []
    params: List[Any] = []

    if not include_archived:
        where.append("c.is_archived=0")


    if status_ids:
        ids = [int(x) for x in status_ids if str(x).isdigit()]
        if ids:
            where.append(f"c.status_id IN ({','.join(['?']*len(ids))})")
            params.extend(ids)

    if deal_type_ids:
        ids = [int(x) for x in deal_type_ids if str(x).isdigit()]
        if ids:
            where.append(f"c.deal_type_id IN ({','.join(['?']*len(ids))})")
            params.extend(ids)

    _apply_filters_to_query(where, params, filters)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    order_sql, _ = build_order_clause(sorts)

    with connect(DB_PATH) as conn:
        rows = conn.execute(
            f"""
            SELECT
                c.id, c.name, COALESCE(c.sales,'') AS sales,
                c.status_id, s.name AS status_name,
                c.deal_type_id, COALESCE(dt.name,'') AS deal_type_name,
                c.priority, COALESCE(c.tags,'') AS tags,
                c.last_contact_at
            FROM clients c
            JOIN statuses s ON s.id=c.status_id
            LEFT JOIN deal_types dt ON dt.id=c.deal_type_id
            {where_sql}
            {order_sql};
            """,
            params,
        ).fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "sales": r["sales"],
                    "status_id": r["status_id"],
                    "status_name": r["status_name"],
                    "deal_type_id": r["deal_type_id"],
                    "deal_type_name": r["deal_type_name"],
                    "priority": int(r["priority"] or 0),
                    "tags": r["tags"],
                    "last_contact_at": r["last_contact_at"],
                    "timeline": _timeline_last_n(conn, int(r["id"]), limit=5),
                }
            )
    if q_cf:
        def _cf(x: Any) -> str:
            return (str(x) if x is not None else "").casefold()

        def _timeline_text(tl: Any) -> str:
            try:
                parts = []
                for e in (tl or []):
                    parts.append(_cf(e.get("date")))
                    parts.append(_cf(e.get("type")))
                    parts.append(_cf(e.get("text")))
                return " ".join(parts)
            except Exception:
                return ""

        out = [
            d for d in out
            if (
                q_cf in _cf(d.get("name"))
                or q_cf in _cf(d.get("sales"))
                or q_cf in _cf(d.get("status_name"))
                or q_cf in _cf(d.get("deal_type_name"))
                or q_cf in _cf(d.get("tags"))
                or q_cf in _timeline_text(d.get("timeline"))
            )
        ]

    return out


@app.post("/api/clients")
async def api_client_create(payload: Dict[str, Any]):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название клиента обязательно")

    sales = (payload.get("sales") or "").strip()
    tags = (payload.get("tags") or "").strip()
    status_id = int(payload.get("status_id") or 0)
    deal_type_id = int(payload.get("deal_type_id") or 0)
    priority = int(payload.get("priority") or 0)
    notes = (payload.get("notes") or "").strip()

    now = utcnow_iso()
    with connect(DB_PATH) as conn:
        if status_id <= 0:
            row = conn.execute("SELECT id FROM statuses WHERE is_active=1 ORDER BY order_index ASC LIMIT 1;").fetchone()
            if not row:
                raise HTTPException(status_code=500, detail="Не настроены статусы")
            status_id = int(row["id"])

        if deal_type_id <= 0:
            row = conn.execute("SELECT id FROM deal_types WHERE is_active=1 ORDER BY order_index ASC LIMIT 1;").fetchone()
            deal_type_id = int(row["id"]) if row else None

        cur = conn.execute(
            """
            INSERT INTO clients(name, sales, status_id, deal_type_id, priority, tags, notes, is_archived, created_at, updated_at, last_contact_at)
            VALUES(?,?,?,?,?,?,?,0,?,?,NULL);
            """,
            (name, sales, status_id, deal_type_id, priority, tags, notes, now, now),
        )
        conn.commit()
        return {"id": cur.lastrowid}


@app.get("/api/clients/{client_id}")
def api_client_get(client_id: int):
    with connect(DB_PATH) as conn:
        c = conn.execute(
            """
            SELECT c.*, s.name AS status_name, dt.name AS deal_type_name
            FROM clients c
            JOIN statuses s ON s.id=c.status_id
            LEFT JOIN deal_types dt ON dt.id=c.deal_type_id
            WHERE c.id=?;
            """,
            (client_id,),
        ).fetchone()
        if not c:
            raise HTTPException(status_code=404, detail="Не найдено")

        ev = conn.execute(
            """
            SELECT id, event_type, event_at, text
            FROM events
            WHERE client_id=?
            ORDER BY event_at DESC, id DESC;
            """,
            (client_id,),
        ).fetchall()

        attachments = _get_client_attachments(conn, client_id)
        by_event: Dict[int, List[Dict[str, Any]]] = {}
        for attachment in attachments:
            if attachment["event_id"] is not None:
                by_event.setdefault(int(attachment["event_id"]), []).append(attachment)

        events = []
        for row in ev:
            event = dict(row)
            event["attachments"] = by_event.get(int(event["id"]), [])
            events.append(event)

        return {"client": dict(c), "events": events, "attachments": attachments}


@app.post("/api/clients/{client_id}/attachments")
async def api_client_attachments_add(
    client_id: int,
    files: List[UploadFile] = File(...),
    paths_json: str = Form(""),
):
    items = await _save_uploaded_attachments(client_id, None, files, _decode_attachment_paths(paths_json))
    return {"ok": True, "items": items}


@app.post("/api/events/{event_id}/attachments")
async def api_event_attachments_add(
    event_id: int,
    files: List[UploadFile] = File(...),
    paths_json: str = Form(""),
):
    with connect(DB_PATH) as conn:
        event = conn.execute("SELECT client_id FROM events WHERE id=?;", (event_id,)).fetchone()
        if not event:
            raise HTTPException(status_code=404, detail="Событие не найдено")
        client_id = int(event["client_id"])

    items = await _save_uploaded_attachments(client_id, event_id, files, _decode_attachment_paths(paths_json))
    return {"ok": True, "items": items}


@app.get("/api/attachments/{attachment_id}/download")
def api_attachment_download(attachment_id: int):
    with connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT id, client_id, original_name, stored_name, mime_type
            FROM attachments
            WHERE id=?;
            """,
            (attachment_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Вложение не найдено")

    path = _attachment_disk_path(int(row["client_id"]), str(row["stored_name"]))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Файл вложения отсутствует")
    return FileResponse(
        path=str(path),
        media_type=row["mime_type"] or "application/octet-stream",
        filename=row["original_name"],
    )


@app.delete("/api/attachments/{attachment_id}")
def api_attachment_delete(attachment_id: int):
    with connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, client_id, stored_name FROM attachments WHERE id=?;",
            (attachment_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Вложение не найдено")
        conn.execute("DELETE FROM attachments WHERE id=?;", (attachment_id,))
        conn.commit()
    _unlink_attachment_rows([row])
    return {"ok": True}


@app.patch("/api/clients/{client_id}")
async def api_client_patch(client_id: int, payload: Dict[str, Any]):
    allowed = {"name", "sales", "status_id", "deal_type_id", "priority", "tags", "notes", "is_archived"}
    fields = {k: v for k, v in payload.items() if k in allowed}
    if not fields:
        return {"ok": True}

    sets: List[str] = []
    params: List[Any] = []
    for k, v in fields.items():
        sets.append(f"{k}=?")
        params.append(v)
    sets.append("updated_at=?")
    params.append(utcnow_iso())
    params.append(client_id)

    with connect(DB_PATH) as conn:
        conn.execute(f"UPDATE clients SET {', '.join(sets)} WHERE id=?;", params)
        conn.commit()
    return {"ok": True}


@app.post("/api/clients/{client_id}/events")
async def api_event_add(client_id: int, payload: Dict[str, Any]):
    event_type = (payload.get("event_type") or "Звонок").strip()
    event_at = (payload.get("event_at") or "").strip()
    text = (payload.get("text") or "").strip()

    if not text:
        raise HTTPException(status_code=400, detail="Текст события обязателен")

    if not event_at:
        event_at = datetime.utcnow().date().isoformat()

    now = utcnow_iso()
    with connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            INSERT INTO events(client_id, event_type, event_at, text, created_at, updated_at)
            VALUES(?,?,?,?,?,?);
            """,
            (client_id, event_type, event_at, text, now, now),
        )
        update_last_contact(conn, client_id)
        conn.commit()
    return {"ok": True, "id": int(cur.lastrowid)}


@app.put("/api/events/{event_id}")
async def api_event_update(event_id: int, payload: Dict[str, Any]):
    event_type = (payload.get("event_type") or "").strip()
    event_at = (payload.get("event_at") or "").strip()
    text = (payload.get("text") or "").strip()

    if not event_type or not event_at or not text:
        raise HTTPException(status_code=400, detail="Все поля обязательны")

    now = utcnow_iso()
    with connect(DB_PATH) as conn:
        row = conn.execute("SELECT client_id FROM events WHERE id=?;", (event_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Не найдено")
        client_id = int(row["client_id"])

        conn.execute(
            "UPDATE events SET event_type=?, event_at=?, text=?, updated_at=? WHERE id=?;",
            (event_type, event_at, text, now, event_id),
        )
        update_last_contact(conn, client_id)
        conn.commit()
    return {"ok": True}


@app.delete("/api/events/{event_id}")
def api_event_delete(event_id: int):
    with connect(DB_PATH) as conn:
        row = conn.execute("SELECT client_id FROM events WHERE id=?;", (event_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Не найдено")
        client_id = int(row["client_id"])
        attachment_rows = conn.execute(
            "SELECT client_id, stored_name FROM attachments WHERE event_id=?;",
            (event_id,),
        ).fetchall()

        conn.execute("DELETE FROM events WHERE id=?;", (event_id,))
        update_last_contact(conn, client_id)
        conn.commit()
    _unlink_attachment_rows(attachment_rows)
    return {"ok": True}


# ----------------------------
# Activity
# ----------------------------
@app.get("/api/activity")
def api_activity(days: int = 7):
    days = int(days or 7)
    with connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.name, COALESCE(c.sales,'') AS sales, c.last_contact_at,
                   s.name AS status_name, COALESCE(dt.name,'') AS deal_type_name
            FROM clients c
            JOIN statuses s ON s.id=c.status_id
            LEFT JOIN deal_types dt ON dt.id=c.deal_type_id
            WHERE c.is_archived=0
              AND (c.last_contact_at IS NULL OR julianday('now') - julianday(c.last_contact_at) >= ?)
            ORDER BY (c.last_contact_at IS NULL) DESC, c.last_contact_at ASC;
            """,
            (days,),
        ).fetchall()
    return [dict(r) for r in rows]


# ----------------------------
# Backup & Export
# ----------------------------
@app.get("/backup")
def do_backup():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot = BACKUP_DIR / f".app_{ts}.db"
    archive = BACKUP_DIR / f"crm_{ts}.zip"
    if DB_PATH.exists():
        with connect(DB_PATH) as source, sqlite3.connect(str(snapshot)) as destination:
            source.backup(destination)
        try:
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.write(snapshot, "data/app.db")
                if ATTACHMENTS_DIR.exists():
                    for file_path in ATTACHMENTS_DIR.rglob("*"):
                        if file_path.is_file():
                            relative = file_path.relative_to(ATTACHMENTS_DIR)
                            bundle.write(file_path, str(Path("data/attachments") / relative))
        finally:
            snapshot.unlink(missing_ok=True)
    return RedirectResponse(url="/", status_code=302)


@app.get("/export/clients.csv")
def export_clients():
    with connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.name, COALESCE(c.sales,'') AS sales, s.name AS status,
                   COALESCE(dt.name,'') AS deal_type,
                   c.priority, COALESCE(c.tags,'') AS tags, c.last_contact_at
            FROM clients c
            JOIN statuses s ON s.id=c.status_id
            LEFT JOIN deal_types dt ON dt.id=c.deal_type_id
            ORDER BY s.order_index ASC, c.last_contact_at DESC;
            """
        ).fetchall()

    header = ["id", "name", "sales", "status", "deal_type", "priority", "tags", "last_contact_at"]
    data = [[r[h] for h in header] for r in rows]
    return StreamingResponse(_csv_stream(data, header), media_type="text/csv")


@app.get("/export/events.csv")
def export_events():
    with connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT e.id, e.client_id, c.name AS client_name, e.event_type, e.event_at, e.text
            FROM events e
            JOIN clients c ON c.id=e.client_id
            ORDER BY e.event_at DESC, e.id DESC;
            """
        ).fetchall()

    header = ["id", "client_id", "client_name", "event_type", "event_at", "text"]
    data = [[r[h] for h in header] for r in rows]
    return StreamingResponse(_csv_stream(data, header), media_type="text/csv")

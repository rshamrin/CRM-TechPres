import sqlite3
from pathlib import Path
from datetime import datetime

DEFAULT_STATUSES = [
    ("Установочная встреча", 10, 1, 0),
    ("Оценка", 20, 1, 0),
    ("Пилот", 30, 1, 0),
    ("Внедрение", 40, 1, 0),
    ("Закупка/Договор", 50, 1, 0),
    ("Завершено", 90, 1, 1),
]

DEFAULT_DEAL_TYPES = [
    ("Пилот", 10, 1),
    ("Консалтинг", 20, 1),
    ("Внедрение", 30, 1),
    ("Продажи", 40, 1),
    ("Другое", 90, 1),
]

DEFAULT_SALES = [
    ("Я", 1),
]


def utcnow_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return any(r["name"] == column for r in rows)


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?;",
        (table,),
    ).fetchone()
    return row is not None


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        cur = conn.cursor()

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS statuses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            order_index INTEGER NOT NULL DEFAULT 100,
            is_active INTEGER NOT NULL DEFAULT 1,
            is_final INTEGER NOT NULL DEFAULT 0
        );
        """
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS deal_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            order_index INTEGER NOT NULL DEFAULT 100,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        """
        )

        # Salespeople dictionary
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS sales_people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        """
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sales TEXT,
            status_id INTEGER NOT NULL,
            deal_type_id INTEGER,
            priority INTEGER NOT NULL DEFAULT 0,
            tags TEXT,
            notes TEXT,
            is_archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_contact_at TEXT,
            FOREIGN KEY(status_id) REFERENCES statuses(id),
            FOREIGN KEY(deal_type_id) REFERENCES deal_types(id)
        );
        """
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_at TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
        );
        """
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            event_id INTEGER,
            original_name TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            stored_name TEXT NOT NULL UNIQUE,
            mime_type TEXT,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE,
            FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
        );
        """
        )

        # Migrations for existing DBs
        if not _has_column(conn, "clients", "deal_type_id"):
            cur.execute("ALTER TABLE clients ADD COLUMN deal_type_id INTEGER;")
        if not _has_column(conn, "clients", "tags"):
            cur.execute("ALTER TABLE clients ADD COLUMN tags TEXT;")
        if not _has_column(conn, "clients", "priority"):
            cur.execute("ALTER TABLE clients ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;")
        if not _has_column(conn, "clients", "is_archived"):
            cur.execute("ALTER TABLE clients ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0;")
        if not _has_column(conn, "clients", "last_contact_at"):
            cur.execute("ALTER TABLE clients ADD COLUMN last_contact_at TEXT;")
        if not _has_column(conn, "clients", "notes"):
            cur.execute("ALTER TABLE clients ADD COLUMN notes TEXT;")

        # Indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_clients_status ON clients(status_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_clients_deal_type ON clients(deal_type_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_clients_last_contact ON clients(last_contact_at);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_client ON events(client_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_event_at ON events(event_at);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_attachments_client ON attachments(client_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_attachments_event ON attachments(event_id);")

        # Seed statuses
        if cur.execute("SELECT COUNT(*) AS c FROM statuses;").fetchone()["c"] == 0:
            cur.executemany(
                "INSERT INTO statuses(name, order_index, is_active, is_final) VALUES(?,?,?,?);",
                DEFAULT_STATUSES,
            )

        # Seed deal types
        if cur.execute("SELECT COUNT(*) AS c FROM deal_types;").fetchone()["c"] == 0:
            cur.executemany(
                "INSERT INTO deal_types(name, order_index, is_active) VALUES(?,?,?);",
                DEFAULT_DEAL_TYPES,
            )

        # Seed sales_people
        if cur.execute("SELECT COUNT(*) AS c FROM sales_people;").fetchone()["c"] == 0:
            # 1) defaults
            cur.executemany(
                "INSERT INTO sales_people(name, is_active) VALUES(?,?);",
                DEFAULT_SALES,
            )
            # 2) pull from existing clients (if any)
            rows = cur.execute(
                "SELECT DISTINCT TRIM(COALESCE(sales,'')) AS s FROM clients WHERE COALESCE(TRIM(sales), '') <> '';"
            ).fetchall()
            for r in rows:
                s = (r["s"] or "").strip()
                if not s:
                    continue
                try:
                    cur.execute("INSERT INTO sales_people(name, is_active) VALUES(?,1);", (s,))
                except Exception:
                    pass

        # Ensure every client has a deal_type_id
        dt = cur.execute(
            "SELECT id FROM deal_types WHERE is_active=1 ORDER BY order_index ASC LIMIT 1;"
        ).fetchone()
        if dt:
            cur.execute("UPDATE clients SET deal_type_id=? WHERE deal_type_id IS NULL;", (int(dt["id"]),))

        conn.commit()


def get_statuses(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT id, name, order_index, is_final FROM statuses WHERE is_active=1 ORDER BY order_index ASC;"
    ).fetchall()


def get_all_statuses(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT id, name, order_index, is_active, is_final FROM statuses ORDER BY order_index ASC, id ASC;"
    ).fetchall()


def get_deal_types(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT id, name, order_index FROM deal_types WHERE is_active=1 ORDER BY order_index ASC;"
    ).fetchall()


def get_all_deal_types(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT id, name, order_index, is_active FROM deal_types ORDER BY order_index ASC, id ASC;"
    ).fetchall()


def get_sales(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT id, name FROM sales_people WHERE is_active=1 ORDER BY name ASC;"
    ).fetchall()


def get_all_sales(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT id, name, is_active FROM sales_people ORDER BY name ASC, id ASC;"
    ).fetchall()


def update_last_contact(conn: sqlite3.Connection, client_id: int) -> None:
    row = conn.execute(
        "SELECT MAX(event_at) AS m FROM events WHERE client_id=?;",
        (client_id,),
    ).fetchone()
    m = row["m"]
    conn.execute(
        "UPDATE clients SET last_contact_at=?, updated_at=? WHERE id=?;",
        (m, utcnow_iso(), client_id),
    )

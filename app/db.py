"""SQLite storage for customers, bookings and conversation state."""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    phone TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    consent_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    service_id TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bookings_start ON bookings (start);
CREATE TABLE IF NOT EXISTS conversations (
    phone TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);
"""

FMT = "%Y-%m-%dT%H:%M"


class Booking(BaseModel):
    id: int
    phone: str
    service_id: str
    start: datetime
    end: datetime


class AgentDB:
    """One connection guarded by a lock; times are naive business-local datetimes."""

    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)

    # customers

    def customer_name(self, phone: str) -> str | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT name FROM customers WHERE phone = ?", (phone,)
            ).fetchone()
        return row["name"] if row else None

    def save_customer(self, phone: str, name: str, now: datetime) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO customers (phone, name, consent_at) VALUES (?, ?, ?) "
                "ON CONFLICT (phone) DO UPDATE SET name = excluded.name",
                (phone, name, now.strftime(FMT)),
            )

    def erase_customer(self, phone: str) -> None:
        """Delete everything stored about this phone number."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            for table in ("customers", "bookings", "conversations"):
                self.conn.execute(f"DELETE FROM {table} WHERE phone = ?", (phone,))
            self.conn.execute("COMMIT")

    # bookings

    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT start, end FROM bookings WHERE status = 'active' AND start < ? AND end > ?",
                (end.strftime(FMT), start.strftime(FMT)),
            ).fetchall()
        return [
            (datetime.strptime(r["start"], FMT), datetime.strptime(r["end"], FMT)) for r in rows
        ]

    def add_booking(
        self, phone: str, service_id: str, start: datetime, end: datetime, now: datetime
    ) -> int | None:
        """Insert the booking, or return None if the range is already taken."""
        s, e = start.strftime(FMT), end.strftime(FMT)
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                clash = self.conn.execute(
                    "SELECT 1 FROM bookings WHERE status = 'active' AND start < ? AND end > ?",
                    (e, s),
                ).fetchone()
                if clash:
                    self.conn.execute("ROLLBACK")
                    return None
                cur = self.conn.execute(
                    "INSERT INTO bookings (phone, service_id, start, end, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (phone, service_id, s, e, now.strftime(FMT)),
                )
                self.conn.execute("COMMIT")
                return cur.lastrowid
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def upcoming(self, phone: str, now: datetime) -> list[Booking]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, phone, service_id, start, end FROM bookings "
                "WHERE phone = ? AND status = 'active' AND start >= ? ORDER BY start",
                (phone, now.strftime(FMT)),
            ).fetchall()
        return [Booking(**dict(r)) for r in rows]

    def on_day(self, day_start: datetime, day_end: datetime) -> list[tuple[Booking, str | None]]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT b.id, b.phone, b.service_id, b.start, b.end, c.name FROM bookings b "
                "LEFT JOIN customers c ON c.phone = b.phone "
                "WHERE b.status = 'active' AND b.start >= ? AND b.start < ? ORDER BY b.start",
                (day_start.strftime(FMT), day_end.strftime(FMT)),
            ).fetchall()
        return [(Booking(**{k: r[k] for k in r.keys() if k != "name"}), r["name"]) for r in rows]

    def cancel_booking(self, phone: str, booking_id: int) -> bool:
        with self.lock:
            cur = self.conn.execute(
                "UPDATE bookings SET status = 'cancelled' "
                "WHERE id = ? AND phone = ? AND status = 'active'",
                (booking_id, phone),
            )
        return cur.rowcount == 1

    # conversation state

    def get_state(self, phone: str) -> tuple[str, dict, datetime] | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT state, data, updated_at FROM conversations WHERE phone = ?", (phone,)
            ).fetchone()
        if not row:
            return None
        return row["state"], json.loads(row["data"]), datetime.strptime(row["updated_at"], FMT)

    def set_state(self, phone: str, state: str, data: dict, now: datetime) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO conversations (phone, state, data, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (phone) DO UPDATE SET state = excluded.state, "
                "data = excluded.data, updated_at = excluded.updated_at",
                (phone, state, json.dumps(data), now.strftime(FMT)),
            )

    def clear_state(self, phone: str) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM conversations WHERE phone = ?", (phone,))

    # webhook retries

    def first_time(self, message_id: str, now: datetime) -> bool:
        """True the first time a message id is seen; WhatsApp retries deliveries."""
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO processed_messages (message_id, received_at) VALUES (?, ?)",
                (message_id, now.strftime(FMT)),
            )
        return cur.rowcount == 1

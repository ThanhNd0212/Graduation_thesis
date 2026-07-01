"""SQLite persistence for completed orders only.

Written exactly once when order_stage transitions to 'done' (order_done or get_direct_recap).
Session logs and conversation history are not stored here.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve.parent.parent / 'chatbot.db'

_SHIP_FEE = 20_000
_GIFT_FEE = 15_000

_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS customers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT,
    phone      TEXT,
    address    TEXT,
    city       TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id    INTEGER NOT NULL REFERENCES customers(id),
    session_id     TEXT    NOT NULL,
    payment_method TEXT,
    gift_wrap      INTEGER NOT NULL DEFAULT 0,
    quantity       INTEGER NOT NULL DEFAULT 1,
    subtotal       INTEGER NOT NULL DEFAULT 0,
    final_total    INTEGER NOT NULL DEFAULT 0,
    delivery_type  TEXT    NOT NULL DEFAULT 'ship',
    created_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     INTEGER NOT NULL REFERENCES orders(id),
    product_id   TEXT,
    product_name TEXT    NOT NULL,
    price        INTEGER NOT NULL DEFAULT 0
);
"""


def _conn -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def init_db -> None:
    """Create tables if they do not exist. Idempotent — safe to call multiple times."""
    with _conn as con:
        con.executescript(_DDL)


def save_order(session) -> int:
    """Persist a completed order to the database. Returns the new order_id.
    Only called when session.order_stage == 'done'.
    """
    cust = session.customer
    is_pickup = (session.payment is None)

    payment_method = session.payment or 'lấy trực tiếp'
    delivery_type  = 'pickup' if is_pickup else 'ship'

    qty         = session.quantity
    subtotal    = session.order_total()
    gift_total  = _GIFT_FEE * len(session.cart) if session.gift_wrap else 0
    ship_total  = 0 if is_pickup else _SHIP_FEE
    final_total = subtotal + gift_total + ship_total

    now = datetime.now.isoformat(sep=' ', timespec='seconds')

    with _conn as con:
        cur = con.cursor

        cur.execute(
            "INSERT INTO customers (name, phone, address, city, created_at) VALUES (?,?,?,?,?)",
            (
                cust.get('NAME',    {}).get('value'),
                cust.get('PHONE',   {}).get('value'),
                cust.get('ADDRESS', {}).get('value'),
                cust.get('CITY',    {}).get('value'),
                now,
            ),
        )
        cust_id = cur.lastrowid

        cur.execute(
            """INSERT INTO orders
               (customer_id, session_id, payment_method, gift_wrap, quantity,
                subtotal, final_total, delivery_type, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (cust_id, session.session_id, payment_method,
             int(session.gift_wrap), qty, subtotal, final_total, delivery_type, now),
        )
        order_id = cur.lastrowid

        for item in session.cart:
            cur.execute(
                "INSERT INTO order_items (order_id, product_id, product_name, price) VALUES (?,?,?,?)",
                (order_id, item.get('product_id'), item['name'], item.get('price', 0)),
            )

        con.commit

    return order_id


def get_orders(limit: int = 100) -> list[dict]:
    """Return the most recent orders with customer info and line items."""
    with _conn as con:
        rows = con.execute(
            """SELECT o.id, o.session_id, o.payment_method, o.gift_wrap,
                      o.quantity, o.subtotal, o.final_total, o.delivery_type, o.created_at,
                      c.name, c.phone, c.address, c.city
               FROM orders o
               JOIN customers c ON c.id = o.customer_id
               ORDER BY o.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall

        result = []
        for r in rows:
            order = dict(r)
            items = con.execute(
                "SELECT product_name, price FROM order_items WHERE order_id=?",
                (order['id'],),
            ).fetchall
            order['items'] = [dict(i) for i in items]
            result.append(order)
        return result

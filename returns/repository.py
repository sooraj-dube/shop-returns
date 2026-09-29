"""SQLite storage for returns and for replaying create responses by idempotency key."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from shop_platform.db import Database
from shop_platform.events import ItemCondition

from returns.domain import Return, ReturnLine, ReturnReason, ReturnStatus

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_SAVE_RETURN = """
INSERT INTO returns (return_id, rma_code, order_id, customer_id, status, currency,
                     refund_cents, created_at, received_at, inspected_at)
VALUES (:return_id, :rma_code, :order_id, :customer_id, :status, :currency,
        :refund_cents, :created_at, :received_at, :inspected_at)
ON CONFLICT (return_id) DO UPDATE SET
    status = excluded.status,
    refund_cents = excluded.refund_cents,
    received_at = excluded.received_at,
    inspected_at = excluded.inspected_at
"""

_SAVE_LINE = """
INSERT INTO return_lines (return_id, line_id, sku, quantity, unit_price_cents, reason,
                          condition, restock, refund_cents)
VALUES (:return_id, :line_id, :sku, :quantity, :unit_price_cents, :reason,
        :condition, :restock, :refund_cents)
ON CONFLICT (return_id, line_id) DO UPDATE SET
    condition = excluded.condition,
    restock = excluded.restock,
    refund_cents = excluded.refund_cents
"""


@dataclass(frozen=True)
class StoredResponse:
    status_code: int
    body: dict[str, Any]


class ReturnsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, ret: Return) -> None:
        """Insert a new return, or write its changed status, timestamps and inspection results."""
        with self._db.transaction() as conn:
            conn.execute(_SAVE_RETURN, _params(ret, skip="lines"))
            conn.executemany(
                _SAVE_LINE,
                [{**_params(line), "return_id": ret.return_id} for line in ret.lines],
            )

    def get(self, return_id: str) -> Return | None:
        with self._db.read() as conn:
            row = conn.execute("SELECT * FROM returns WHERE return_id = ?", (return_id,)).fetchone()
            return None if row is None else _load(conn, row)

    def list_for_order(self, order_id: str) -> list[Return]:
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM returns WHERE order_id = ? ORDER BY created_at, rowid", (order_id,)
            ).fetchall()
            return [_load(conn, row) for row in rows]

    def returned_quantities(self, order_id: str) -> dict[str, int]:
        """Quantity per order line held by the order's returns that are not cancelled."""
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT l.line_id, SUM(l.quantity) AS quantity FROM return_lines l"
                " JOIN returns r ON r.return_id = l.return_id"
                " WHERE r.order_id = ? AND r.status != ? GROUP BY l.line_id",
                (order_id, ReturnStatus.CANCELLED),
            ).fetchall()
        return {row["line_id"]: row["quantity"] for row in rows}

    def rma_code_taken(self, rma_code: str) -> bool:
        with self._db.read() as conn:
            row = conn.execute("SELECT 1 FROM returns WHERE rma_code = ?", (rma_code,)).fetchone()
        return row is not None

    def get_response(self, idempotency_key: str) -> StoredResponse | None:
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT status_code, response_body FROM idempotency_keys WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        return StoredResponse(row["status_code"], json.loads(row["response_body"]))

    def save_response(self, key: str, response: StoredResponse, created_at: datetime) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO idempotency_keys (idempotency_key, status_code, response_body,"
                " created_at) VALUES (?, ?, ?, ?)",
                (key, response.status_code, json.dumps(response.body), created_at.isoformat()),
            )


def _load(conn: sqlite3.Connection, row: sqlite3.Row) -> Return:
    lines = conn.execute(
        "SELECT * FROM return_lines WHERE return_id = ? ORDER BY rowid", (row["return_id"],)
    )
    return Return(
        **{
            **dict(row),
            "status": ReturnStatus(row["status"]),
            "lines": tuple(_line(line) for line in lines),
            "created_at": datetime.fromisoformat(row["created_at"]),
            "received_at": _parse(row["received_at"]),
            "inspected_at": _parse(row["inspected_at"]),
        }
    )


def _line(row: sqlite3.Row) -> ReturnLine:
    return ReturnLine(
        line_id=row["line_id"],
        sku=row["sku"],
        quantity=row["quantity"],
        unit_price_cents=row["unit_price_cents"],
        reason=ReturnReason(row["reason"]),
        condition=None if row["condition"] is None else ItemCondition(row["condition"]),
        restock=None if row["restock"] is None else bool(row["restock"]),
        refund_cents=row["refund_cents"],
    )


def _params(obj: object, skip: str = "") -> dict[str, Any]:
    return {
        name: value.isoformat() if isinstance(value, datetime) else value
        for name, value in vars(obj).items()
        if name != skip
    }


def _parse(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)

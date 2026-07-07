from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..models.table import Table

_TAKEAWAY_VALUES = {"", "takeaway", "delivery", "none", "null"}


def is_takeaway_table_reference(value: Any) -> bool:
    if value is None:
        return True

    normalized = str(value).strip().lower()
    return normalized in _TAKEAWAY_VALUES


def parse_numeric_table_id(value: Any) -> int | None:
    if is_takeaway_table_reference(value):
        return None

    normalized = str(value).strip()
    try:
        return int(normalized)
    except (TypeError, ValueError):
        return None


def build_table_number_map(db: Session, orders: Iterable[Any]) -> dict[int, str]:
    table_ids = sorted(
        {
            parsed_id
            for order in orders
            if (parsed_id := parse_numeric_table_id(getattr(order, "table_id", None))) is not None
        }
    )

    if not table_ids:
        return {}

    tables = db.query(Table).filter(Table.id.in_(table_ids)).all()
    return {table.id: table.table_number for table in tables}


def resolve_order_table_number(order: Any, table_number_map: dict[int, str]) -> str:
    raw_table_id = getattr(order, "table_id", None)

    if is_takeaway_table_reference(raw_table_id):
        return "Takeaway"

    parsed_id = parse_numeric_table_id(raw_table_id)
    if parsed_id is not None:
        return table_number_map.get(parsed_id, "N/A")

    return str(raw_table_id)


"""Returns, their lines, and how return requests are checked and refunds worked out."""

import secrets
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from shop_platform.clients import Order, OrderStatus
from shop_platform.events import ItemCondition

RETURN_ID_PREFIX = "ret"
RMA_PREFIX = "RMA-"
RMA_LENGTH = 6
# Leaves out 0/O and 1/I/L so a code read off a parcel label is not misread.
RMA_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


class ReturnStatus(StrEnum):
    REQUESTED = "requested"
    RECEIVED = "received"
    INSPECTED = "inspected"
    CANCELLED = "cancelled"


class ReturnReason(StrEnum):
    DAMAGED_IN_TRANSIT = "damaged_in_transit"
    WRONG_ITEM = "wrong_item"
    DEFECTIVE = "defective"
    NO_LONGER_NEEDED = "no_longer_needed"


# Reasons that are refunded in full whatever condition the items come back in.
FULL_REFUND_REASONS = frozenset(
    {ReturnReason.DAMAGED_IN_TRANSIT, ReturnReason.WRONG_ITEM, ReturnReason.DEFECTIVE}
)

# The status a return must be in to move to each target status.
REQUIRED_STATUS: dict[ReturnStatus, ReturnStatus] = {
    ReturnStatus.RECEIVED: ReturnStatus.REQUESTED,
    ReturnStatus.CANCELLED: ReturnStatus.REQUESTED,
    ReturnStatus.INSPECTED: ReturnStatus.RECEIVED,
}


class ErrorCode(StrEnum):
    RETURN_NOT_FOUND = "return_not_found"
    ORDER_NOT_FOUND = "order_not_found"
    NOT_YOUR_ORDER = "not_your_order"
    ORDER_NOT_SHIPPED = "order_not_shipped"
    RETURN_WINDOW_CLOSED = "return_window_closed"
    UNKNOWN_LINE = "unknown_line"
    DUPLICATE_LINE = "duplicate_line"
    QUANTITY_EXCEEDS_ORDER = "quantity_exceeds_order"
    INVALID_RETURN_STATE = "invalid_return_state"
    INVALID_INSPECTION = "invalid_inspection"
    ORDERS_UNAVAILABLE = "orders_unavailable"


class ReturnsError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RequestedLine:
    line_id: str
    quantity: int
    reason: ReturnReason


@dataclass(frozen=True)
class LineInspection:
    line_id: str
    condition: ItemCondition


@dataclass(frozen=True)
class ReturnLine:
    line_id: str
    sku: str
    quantity: int
    unit_price_cents: int
    reason: ReturnReason
    condition: ItemCondition | None = None
    restock: bool | None = None
    refund_cents: int | None = None


@dataclass(frozen=True)
class Return:
    return_id: str
    rma_code: str
    order_id: str
    customer_id: str
    status: ReturnStatus
    currency: str
    lines: tuple[ReturnLine, ...]
    created_at: datetime
    refund_cents: int | None = None
    received_at: datetime | None = None
    inspected_at: datetime | None = None


@dataclass(frozen=True)
class LineRefund:
    refund_cents: int
    restock: bool


def new_rma_code() -> str:
    return RMA_PREFIX + "".join(secrets.choice(RMA_ALPHABET) for _ in range(RMA_LENGTH))


def check_order_returnable(order: Order, customer_id: str, now: datetime, window_days: int) -> None:
    if order.customer_id != customer_id:
        raise ReturnsError(
            ErrorCode.NOT_YOUR_ORDER, f"order {order.order_id} belongs to another customer"
        )
    if order.status != OrderStatus.SHIPPED or order.shipped_at is None:
        raise ReturnsError(
            ErrorCode.ORDER_NOT_SHIPPED, f"order {order.order_id} is {order.status}, not shipped"
        )
    deadline = order.shipped_at + timedelta(days=window_days)
    if now > deadline:
        raise ReturnsError(
            ErrorCode.RETURN_WINDOW_CLOSED,
            f"returns for order {order.order_id} closed at {deadline.isoformat()}",
        )


def build_return_lines(
    order: Order, requested: Sequence[RequestedLine], already_returned: Mapping[str, int]
) -> tuple[ReturnLine, ...]:
    """Lines for a new return, checked against the order.

    ``already_returned`` is the quantity per order line held by returns that are not cancelled.
    """
    ordered = {line.line_id: line for line in order.lines}
    lines: list[ReturnLine] = []
    for item in requested:
        if any(line.line_id == item.line_id for line in lines):
            raise ReturnsError(
                ErrorCode.DUPLICATE_LINE, f"line {item.line_id} appears more than once"
            )
        line = ordered.get(item.line_id)
        if line is None:
            raise ReturnsError(
                ErrorCode.UNKNOWN_LINE, f"order {order.order_id} has no line {item.line_id}"
            )
        left = line.quantity - already_returned.get(item.line_id, 0)
        if item.quantity > left:
            raise ReturnsError(
                ErrorCode.QUANTITY_EXCEEDS_ORDER,
                f"line {item.line_id}: {item.quantity} requested, {left} of "
                f"{line.quantity} left to return",
            )
        lines.append(
            ReturnLine(line.line_id, line.sku, item.quantity, line.unit_price_cents, item.reason)
        )
    return tuple(lines)


def restocking_fee_cents(amount_cents: int, percent: int) -> int:
    """``percent`` of ``amount_cents``, rounded half up to the cent."""
    return (amount_cents * percent + 50) // 100


def refund_for(line: ReturnLine, condition: ItemCondition, fee_percent: int) -> LineRefund:
    full = line.unit_price_cents * line.quantity
    if line.reason in FULL_REFUND_REASONS:
        return LineRefund(full, restock=condition == ItemCondition.NEW)
    if condition == ItemCondition.NEW:
        return LineRefund(full, restock=True)
    if condition == ItemCondition.OPENED:
        return LineRefund(full - restocking_fee_cents(full, fee_percent), restock=True)
    return LineRefund(0, restock=False)


def apply_inspection(
    lines: Sequence[ReturnLine], inspections: Sequence[LineInspection], fee_percent: int
) -> tuple[ReturnLine, ...]:
    """Record each line's condition and refund. Every line must be inspected exactly once."""
    counts = Counter(item.line_id for item in inspections)
    expected = {line.line_id for line in lines}
    if counts.keys() != expected or max(counts.values(), default=0) > 1:
        raise ReturnsError(
            ErrorCode.INVALID_INSPECTION,
            f"each of {sorted(expected)} must be inspected exactly once, got {dict(counts)}",
        )
    conditions = {item.line_id: item.condition for item in inspections}
    inspected: list[ReturnLine] = []
    for line in lines:
        condition = conditions[line.line_id]
        refund = refund_for(line, condition, fee_percent)
        inspected.append(
            replace(
                line, condition=condition, restock=refund.restock, refund_cents=refund.refund_cents
            )
        )
    return tuple(inspected)


def check_transition(ret: Return, target: ReturnStatus) -> None:
    required = REQUIRED_STATUS[target]
    if ret.status != required:
        raise ReturnsError(
            ErrorCode.INVALID_RETURN_STATE,
            f"return {ret.return_id} is {ret.status}; it must be {required} to become {target}",
        )

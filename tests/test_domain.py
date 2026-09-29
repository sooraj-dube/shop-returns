from datetime import timedelta

import pytest
from shop_platform.clients import OrderStatus
from shop_platform.events import ItemCondition

from returns.domain import (
    ErrorCode,
    LineInspection,
    LineRefund,
    RequestedLine,
    Return,
    ReturnLine,
    ReturnReason,
    ReturnsError,
    ReturnStatus,
    apply_inspection,
    build_return_lines,
    check_order_returnable,
    check_transition,
    refund_for,
    restocking_fee_cents,
)
from tests.fakes import CUSTOMER_ID, SHIPPED_AT, make_order

NOW = SHIPPED_AT + timedelta(days=5)
Reason = ReturnReason
Condition = ItemCondition


def raises(code: ErrorCode):
    return pytest.raises(ReturnsError, match=f"^{code}:")


@pytest.mark.parametrize(
    ("order", "code"),
    [
        (make_order(customer_id="cus_other"), ErrorCode.NOT_YOUR_ORDER),
        (make_order(status=OrderStatus.CONFIRMED, shipped_at=None), ErrorCode.ORDER_NOT_SHIPPED),
        (make_order(status=OrderStatus.CANCELLED, shipped_at=None), ErrorCode.ORDER_NOT_SHIPPED),
        (make_order(shipped_at=NOW - timedelta(days=31)), ErrorCode.RETURN_WINDOW_CLOSED),
    ],
)
def test_order_that_cannot_be_returned(order, code):
    with raises(code):
        check_order_returnable(order, CUSTOMER_ID, NOW, 30)


def test_return_window_is_still_open_at_its_last_moment():
    order = make_order(shipped_at=NOW - timedelta(days=30))
    check_order_returnable(order, CUSTOMER_ID, NOW, 30)
    with raises(ErrorCode.RETURN_WINDOW_CLOSED):
        check_order_returnable(order, CUSTOMER_ID, NOW + timedelta(seconds=1), 30)


@pytest.mark.parametrize(
    ("line_id", "quantity", "already_returned", "code"),
    [
        ("line_hat", 1, 0, ErrorCode.UNKNOWN_LINE),
        ("line_socks", 4, 0, ErrorCode.QUANTITY_EXCEEDS_ORDER),
        ("line_socks", 2, 2, ErrorCode.QUANTITY_EXCEEDS_ORDER),
    ],
)
def test_requested_line_is_checked_against_the_order(line_id, quantity, already_returned, code):
    requested = [RequestedLine(line_id, quantity, Reason.DEFECTIVE)]
    with raises(code):
        build_return_lines(make_order(), requested, {"line_socks": already_returned})


def test_return_lines_copy_the_order_price_and_reject_repeated_lines():
    requested = [RequestedLine("line_socks", 1, Reason.WRONG_ITEM)]
    lines = build_return_lines(make_order(), requested, {"line_socks": 2})
    assert lines == (ReturnLine("line_socks", "SOCK-M", 1, 1_495, Reason.WRONG_ITEM),)
    with raises(ErrorCode.DUPLICATE_LINE):
        build_return_lines(make_order(), requested * 2, {})


@pytest.mark.parametrize(
    ("reason", "condition", "refund_cents", "restock"),
    [
        (Reason.DEFECTIVE, Condition.NEW, 2_000, True),
        (Reason.DEFECTIVE, Condition.OPENED, 2_000, False),
        (Reason.WRONG_ITEM, Condition.OPENED, 2_000, False),
        (Reason.WRONG_ITEM, Condition.NEW, 2_000, True),
        (Reason.DAMAGED_IN_TRANSIT, Condition.DAMAGED, 2_000, False),
        (Reason.NO_LONGER_NEEDED, Condition.NEW, 2_000, True),
        (Reason.NO_LONGER_NEEDED, Condition.OPENED, 1_800, True),
        (Reason.NO_LONGER_NEEDED, Condition.DAMAGED, 0, False),
    ],
)
def test_refund_and_restock_per_reason_and_condition(reason, condition, refund_cents, restock):
    line = ReturnLine("line_1", "SKU-1", 2, 1_000, reason)
    assert refund_for(line, condition, 10) == LineRefund(refund_cents, restock)


@pytest.mark.parametrize(("amount", "fee"), [(1_000, 100), (1_005, 101), (1_004, 100), (0, 0)])
def test_restocking_fee_rounds_half_up_to_the_cent(amount, fee):
    assert restocking_fee_cents(amount, 10) == fee


@pytest.mark.parametrize(
    "line_ids",
    [
        ["line_boots"],
        ["line_boots", "line_boots", "line_socks"],
        ["line_boots", "line_socks", "line_hat"],
    ],
)
def test_inspection_must_cover_every_line_exactly_once(line_ids):
    lines = [
        ReturnLine("line_boots", "BOOT-42", 1, 12_999, Reason.DEFECTIVE),
        ReturnLine("line_socks", "SOCK-M", 1, 1_495, Reason.DEFECTIVE),
    ]
    inspections = [LineInspection(line_id, Condition.NEW) for line_id in line_ids]
    with raises(ErrorCode.INVALID_INSPECTION):
        apply_inspection(lines, inspections, 10)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ReturnStatus.REQUESTED, ReturnStatus.INSPECTED),
        (ReturnStatus.RECEIVED, ReturnStatus.CANCELLED),
        (ReturnStatus.RECEIVED, ReturnStatus.RECEIVED),
        (ReturnStatus.CANCELLED, ReturnStatus.RECEIVED),
        (ReturnStatus.INSPECTED, ReturnStatus.INSPECTED),
    ],
)
def test_out_of_order_transition_is_rejected(current, target):
    ret = Return("ret_1", "RMA-222222", "ord_1", CUSTOMER_ID, current, "EUR", (), NOW)
    with raises(ErrorCode.INVALID_RETURN_STATE):
        check_transition(ret, target)

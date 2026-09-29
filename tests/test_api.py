import re
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from returns.app import create_app
from returns.service import ReturnsService
from tests.fakes import CUSTOMER_ID, SHIPPED_AT, make_order

BOOTS = {"line_id": "line_boots", "quantity": 1, "reason": "no_longer_needed"}
SOCKS = {"line_id": "line_socks", "quantity": 2, "reason": "defective"}


def request_return(client, lines=(BOOTS,), *, key=None, order_id="ord_1", customer_id=CUSTOMER_ID):
    return client.post(
        "/returns",
        json={"order_id": order_id, "customer_id": customer_id, "lines": list(lines)},
        headers={"Idempotency-Key": key or f"key-{uuid.uuid4().hex}"},
    )


def error(response):
    return response.status_code, response.json()["error"]["code"]


def test_request_opens_a_return_priced_from_the_order(client):
    response = request_return(client, [BOOTS, SOCKS])
    assert response.status_code == 201
    body = response.json()
    assert body["return_id"].startswith("ret_")
    assert re.fullmatch(r"RMA-[2-9A-HJKMNP-Z]{6}", body["rma_code"])
    assert (body["status"], body["currency"], body["refund_cents"]) == ("requested", "EUR", None)
    assert [(ln["sku"], ln["quantity"], ln["unit_price_cents"]) for ln in body["lines"]] == [
        ("BOOT-42", 1, 12_999),
        ("SOCK-M", 2, 1_495),
    ]
    assert client.get(f"/returns/{body['return_id']}").json() == body


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"order_id": "ord_missing"}, (404, "order_not_found")),
        ({"customer_id": "cus_other"}, (403, "not_your_order")),
        ({"order_id": "ord_unshipped"}, (409, "order_not_shipped")),
        ({"order_id": "ord_old"}, (409, "return_window_closed")),
        ({"lines": [{**BOOTS, "line_id": "line_hat"}]}, (422, "unknown_line")),
        ({"lines": [BOOTS, BOOTS]}, (422, "duplicate_line")),
        ({"lines": [{**BOOTS, "quantity": 3}]}, (409, "quantity_exceeds_order")),
    ],
)
def test_request_is_rejected(client, orders, changes, expected):
    orders.orders["ord_unshipped"] = make_order(
        "ord_unshipped", status="confirmed", shipped_at=None
    )
    orders.orders["ord_old"] = make_order("ord_old", shipped_at=SHIPPED_AT - timedelta(days=30))
    assert error(request_return(client, **changes)) == expected


def test_quantity_is_shared_across_returns_until_one_is_cancelled(client):
    first = request_return(client, [SOCKS]).json()
    assert error(request_return(client, [SOCKS])) == (409, "quantity_exceeds_order")
    assert request_return(client, [{**SOCKS, "quantity": 1}]).status_code == 201
    assert client.post(f"/returns/{first['return_id']}/cancel").json()["status"] == "cancelled"
    assert request_return(client, [SOCKS]).status_code == 201
    statuses = [ret["status"] for ret in client.get("/orders/ord_1/returns").json()["returns"]]
    assert statuses == ["cancelled", "requested", "requested"]


def test_orders_unavailable_answers_503(client, orders, caplog):
    orders.unavailable = True
    assert error(request_return(client)) == (503, "orders_unavailable")
    assert "orders unavailable" in caplog.text


def test_repeated_key_replays_the_first_response_without_a_second_return(client, orders):
    first = request_return(client, key="key-return-1")
    orders.unavailable = True
    replay = request_return(client, key="key-return-1")
    assert (replay.status_code, replay.json()) == (201, first.json())
    assert len(client.get("/orders/ord_1/returns").json()["returns"]) == 1


def test_rma_code_already_in_use_is_not_given_out_again(db, orders, clock):
    codes = iter(["RMA-AAAAAA", "RMA-AAAAAA", "RMA-BBBBBB"])
    service = ReturnsService(db, orders, clock, rma_codes=lambda: next(codes))
    with TestClient(create_app(service)) as client:
        first, second = request_return(client), request_return(client)
    assert [first.json()["rma_code"], second.json()["rma_code"]] == ["RMA-AAAAAA", "RMA-BBBBBB"]


def test_receive_records_the_time_and_is_allowed_once(client, clock):
    return_id = request_return(client).json()["return_id"]
    received = client.post(f"/returns/{return_id}/receive").json()
    assert received["status"] == "received"
    assert datetime.fromisoformat(received["received_at"]) == clock.now()
    for action in ("receive", "cancel"):
        response = client.post(f"/returns/{return_id}/{action}")
        assert error(response) == (409, "invalid_return_state")
    assert error(client.post("/returns/ret_missing/receive")) == (404, "return_not_found")

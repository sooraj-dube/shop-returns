from datetime import UTC, datetime, timedelta

from shop_platform.clients import Order, OrderLine, OrdersClient, OrderStatus
from shop_platform.events import ShippingAddress
from shop_platform.http import NotFound, ServiceUnavailable

CUSTOMER_ID = "cus_anna"
SHIPPED_AT = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
ADDRESS = ShippingAddress(
    name="Anna Berg", line1="Keizersgracht 12", city="Amsterdam", postal_code="1015", country="NL"
)


def make_order(
    order_id: str = "ord_1",
    *,
    customer_id: str = CUSTOMER_ID,
    status: str = OrderStatus.SHIPPED.value,
    shipped_at: datetime | None = SHIPPED_AT,
) -> Order:
    lines = [
        OrderLine(line_id="line_boots", sku="BOOT-42", name="Trail boots", quantity=2,
                  unit_price_cents=12_999, reservation_id="res_boots"),
        OrderLine(line_id="line_socks", sku="SOCK-M", name="Merino socks", quantity=3,
                  unit_price_cents=1_495, reservation_id="res_socks"),
    ]  # fmt: skip
    total = sum(line.quantity * line.unit_price_cents for line in lines)
    return Order(
        order_id=order_id, customer_id=customer_id, status=status, payment_method="card",
        currency="EUR", lines=lines, subtotal_cents=total, discount_cents=0, shipping_cents=0,
        tax_cents=0, total_cents=total, shipping_address=ADDRESS,
        placed_at=SHIPPED_AT - timedelta(days=2), shipped_at=shipped_at,
    )  # fmt: skip


class FakeOrdersClient(OrdersClient):
    def __init__(self, *orders: Order) -> None:
        self.orders = {order.order_id: order for order in orders}
        self.unavailable = False

    def get_order(self, order_id: str) -> Order:
        if self.unavailable:
            raise ServiceUnavailable("orders unavailable after 3 attempt(s)")
        order = self.orders.get(order_id)
        if order is None:
            raise NotFound(404, "order_not_found", f"no order {order_id}")
        return order

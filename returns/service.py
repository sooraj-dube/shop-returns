import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from shop_platform.clients import Order, OrdersClient
from shop_platform.clock import Clock
from shop_platform.db import Database
from shop_platform.events import RETURNS_STREAM, InspectedLine, ReturnInspected
from shop_platform.http import NotFound, ServiceError, ServiceUnavailable
from shop_platform.ids import new_id
from shop_platform.messaging import EventPublisher

from returns import domain
from returns.domain import (
    ErrorCode,
    LineInspection,
    RequestedLine,
    Return,
    ReturnsError,
    ReturnStatus,
)
from returns.repository import ReturnsRepository, StoredResponse
from returns.schemas import ReturnOut

logger = logging.getLogger(__name__)

CREATED = 201
RMA_CODE_ATTEMPTS = 10


class ReturnsService:
    def __init__(
        self,
        db: Database,
        orders: OrdersClient,
        publisher: EventPublisher,
        clock: Clock,
        *,
        return_window_days: int = 30,
        restocking_fee_percent: int = 10,
        rma_codes: Callable[[], str] = domain.new_rma_code,
    ) -> None:
        self._db = db
        self._repo = ReturnsRepository(db)
        self._orders = orders
        self._publisher = publisher
        self._clock = clock
        self._window_days = return_window_days
        self._fee_percent = restocking_fee_percent
        self._rma_codes = rma_codes

    def create_return(
        self,
        order_id: str,
        customer_id: str,
        lines: Sequence[RequestedLine],
        *,
        idempotency_key: str,
    ) -> StoredResponse:
        """Open a return, or answer with the stored response when the key was seen before."""
        stored = self._repo.get_response(idempotency_key)
        if stored is not None:
            return stored
        order = self._fetch_order(order_id)
        now = self._clock.now()
        domain.check_order_returnable(order, customer_id, now, self._window_days)
        with self._db.transaction():
            # Another request with the same key may have finished while the order was fetched.
            stored = self._repo.get_response(idempotency_key)
            if stored is not None:
                return stored
            returned = self._repo.returned_quantities(order.order_id)
            ret = Return(
                return_id=new_id(domain.RETURN_ID_PREFIX),
                rma_code=self._unused_rma_code(),
                order_id=order.order_id,
                customer_id=customer_id,
                status=ReturnStatus.REQUESTED,
                currency=order.currency,
                lines=domain.build_return_lines(order, lines, returned),
                created_at=now,
            )
            self._repo.save(ret)
            body = ReturnOut.model_validate(ret).model_dump(mode="json")
            stored = StoredResponse(CREATED, body)
            self._repo.save_response(idempotency_key, stored, now)
        logger.info(
            "return requested",
            extra={"return_id": ret.return_id, "order_id": ret.order_id, "rma_code": ret.rma_code},
        )
        return stored

    def get_return(self, return_id: str) -> Return:
        ret = self._repo.get(return_id)
        if ret is None:
            raise ReturnsError(ErrorCode.RETURN_NOT_FOUND, f"no return {return_id}")
        return ret

    def list_for_order(self, order_id: str) -> list[Return]:
        return self._repo.list_for_order(order_id)

    def cancel(self, return_id: str) -> Return:
        return self._move(return_id, ReturnStatus.CANCELLED)

    def receive(self, return_id: str) -> Return:
        return self._move(return_id, ReturnStatus.RECEIVED, received_at=self._clock.now())

    def inspect(self, return_id: str, inspections: Sequence[LineInspection]) -> Return:
        """Record each line's condition and refund, then publish ReturnInspected."""
        with self._db.transaction():
            ret = self.get_return(return_id)
            domain.check_transition(ret, ReturnStatus.INSPECTED)
            lines = domain.apply_inspection(ret.lines, inspections, self._fee_percent)
            ret = replace(
                ret,
                status=ReturnStatus.INSPECTED,
                lines=lines,
                refund_cents=sum(line.refund_cents or 0 for line in lines),
                inspected_at=self._clock.now(),
            )
            self._repo.save(ret)
        logger.info(
            "return inspected", extra={"return_id": return_id, "refund_cents": ret.refund_cents}
        )
        self._publish_inspected(ret)
        return ret

    def _move(self, return_id: str, target: ReturnStatus, **changes: Any) -> Return:
        with self._db.transaction():
            ret = self.get_return(return_id)
            domain.check_transition(ret, target)
            ret = replace(ret, status=target, **changes)
            self._repo.save(ret)
        logger.info("return status changed", extra={"return_id": return_id, "status": target})
        return ret

    def _publish_inspected(self, ret: Return) -> None:
        event = ReturnInspected(
            return_id=ret.return_id,
            order_id=ret.order_id,
            occurred_at=ret.inspected_at,
            lines=[InspectedLine.model_validate(line, from_attributes=True) for line in ret.lines],
        )
        try:
            self._publisher.publish(RETURNS_STREAM, event)
        except Exception:
            logger.exception(
                "publishing return.inspected failed",
                extra={"return_id": ret.return_id, "event_id": event.event_id},
            )
            raise

    def _fetch_order(self, order_id: str) -> Order:
        try:
            return self._orders.get_order(order_id)
        except NotFound as exc:
            raise ReturnsError(ErrorCode.ORDER_NOT_FOUND, f"no order {order_id}") from exc
        except ServiceUnavailable as exc:
            logger.warning("orders unavailable", extra={"order_id": order_id, "error": str(exc)})
            raise ReturnsError(
                ErrorCode.ORDERS_UNAVAILABLE, "could not look up the order, try again later"
            ) from exc
        except ServiceError:
            logger.exception("unexpected answer from orders", extra={"order_id": order_id})
            raise

    def _unused_rma_code(self) -> str:
        for _ in range(RMA_CODE_ATTEMPTS):
            code = self._rma_codes()
            if not self._repo.rma_code_taken(code):
                return code
        logger.error("no unused RMA code found", extra={"attempts": RMA_CODE_ATTEMPTS})
        raise RuntimeError(f"no unused RMA code after {RMA_CODE_ATTEMPTS} attempts")

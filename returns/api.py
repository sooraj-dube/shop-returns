import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from shop_platform.web import ApiError, IdempotencyKey

from returns.domain import ErrorCode, RequestedLine, ReturnsError
from returns.schemas import CreateReturnIn, ReturnListOut, ReturnOut
from returns.service import ReturnsService

logger = logging.getLogger(__name__)

HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.RETURN_NOT_FOUND: 404,
    ErrorCode.ORDER_NOT_FOUND: 404,
    ErrorCode.NOT_YOUR_ORDER: 403,
    ErrorCode.ORDER_NOT_SHIPPED: 409,
    ErrorCode.RETURN_WINDOW_CLOSED: 409,
    ErrorCode.UNKNOWN_LINE: 422,
    ErrorCode.DUPLICATE_LINE: 422,
    ErrorCode.QUANTITY_EXCEEDS_ORDER: 409,
    ErrorCode.INVALID_RETURN_STATE: 409,
    ErrorCode.INVALID_INSPECTION: 422,
    ErrorCode.ORDERS_UNAVAILABLE: 503,
}

router = APIRouter()


def get_service(request: Request) -> ReturnsService:
    return request.app.state.service


Service = Annotated[ReturnsService, Depends(get_service)]


@contextmanager
def domain_errors() -> Iterator[None]:
    try:
        yield
    except ReturnsError as exc:
        logger.info("request rejected", extra={"code": exc.code, "reason": exc.message})
        raise ApiError(HTTP_STATUS[exc.code], exc.code, exc.message) from exc


@router.post("/returns", status_code=201, response_model=ReturnOut)
def create_return(
    body: CreateReturnIn, idempotency_key: IdempotencyKey, service: Service
) -> JSONResponse:
    lines = [RequestedLine(**line.model_dump()) for line in body.lines]
    with domain_errors():
        stored = service.create_return(
            body.order_id, body.customer_id, lines, idempotency_key=idempotency_key
        )
    return JSONResponse(status_code=stored.status_code, content=stored.body)


@router.get("/returns/{return_id}")
def get_return(return_id: str, service: Service) -> ReturnOut:
    with domain_errors():
        return ReturnOut.model_validate(service.get_return(return_id))


@router.get("/orders/{order_id}/returns")
def list_order_returns(order_id: str, service: Service) -> ReturnListOut:
    returns = service.list_for_order(order_id)
    return ReturnListOut(returns=[ReturnOut.model_validate(ret) for ret in returns])


@router.post("/returns/{return_id}/cancel")
def cancel_return(return_id: str, service: Service) -> ReturnOut:
    with domain_errors():
        return ReturnOut.model_validate(service.cancel(return_id))


@router.post("/returns/{return_id}/receive")
def receive_return(return_id: str, service: Service) -> ReturnOut:
    with domain_errors():
        return ReturnOut.model_validate(service.receive(return_id))

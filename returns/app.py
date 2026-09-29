from fastapi import FastAPI
from shop_platform.clients import OrdersClient
from shop_platform.clock import SystemClock
from shop_platform.db import Database
from shop_platform.logging import configure_logging
from shop_platform.messaging import RedisStreamPublisher, redis_from_url
from shop_platform.web import health_router, install_error_handlers

from returns.api import router
from returns.config import Settings
from returns.repository import MIGRATIONS_DIR
from returns.service import ReturnsService

SERVICE_NAME = "returns"


def build_service(settings: Settings) -> ReturnsService:
    db = Database(settings.database_path)
    db.migrate(MIGRATIONS_DIR)
    return ReturnsService(
        db,
        OrdersClient(settings.orders_url),
        RedisStreamPublisher(redis_from_url(settings.redis_url)),
        SystemClock(),
        return_window_days=settings.return_window_days,
        restocking_fee_percent=settings.restocking_fee_percent,
    )


def create_app(service: ReturnsService | None = None) -> FastAPI:
    if service is None:
        settings = Settings.from_env()
        configure_logging(SERVICE_NAME, settings.log_level)
        service = build_service(settings)
    app = FastAPI(title="shop-returns", version="1.0.0")
    app.state.service = service
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(router)
    return app

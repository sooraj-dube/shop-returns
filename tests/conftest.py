from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from shop_platform.clock import FixedClock
from shop_platform.db import Database
from shop_platform.messaging import InMemoryPublisher

from returns.app import create_app
from returns.repository import MIGRATIONS_DIR
from returns.service import ReturnsService
from tests.fakes import SHIPPED_AT, FakeOrdersClient, make_order


@pytest.fixture
def clock():
    return FixedClock(SHIPPED_AT + timedelta(days=3))


@pytest.fixture
def orders():
    return FakeOrdersClient(make_order())


@pytest.fixture
def publisher():
    return InMemoryPublisher()


@pytest.fixture
def db():
    db = Database(":memory:")
    db.migrate(MIGRATIONS_DIR)
    yield db
    db.close()


@pytest.fixture
def service(db, orders, publisher, clock):
    return ReturnsService(
        db, orders, publisher, clock, return_window_days=30, restocking_fee_percent=10
    )


@pytest.fixture
def client(service):
    with TestClient(create_app(service)) as client:
        yield client

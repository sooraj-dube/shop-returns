# shop-returns

Customer returns: a customer requests a return for items from a shipped order, the parcel is
received, the items are inspected and the refund amount is worked out.

## HTTP API

| Method | Path | Description |
|---|---|---|
| POST | `/returns` | Request a return. Needs an `Idempotency-Key` header. Body: `order_id`, `customer_id`, `lines` of `{line_id, quantity, reason}` |
| GET | `/returns/{return_id}` | A return with its lines, RMA code and refund |
| GET | `/orders/{order_id}/returns` | All returns for an order |
| POST | `/returns/{return_id}/cancel` | Cancel a return that is still `requested` |
| POST | `/returns/{return_id}/receive` | Mark the parcel as received |
| POST | `/returns/{return_id}/inspect` | Body: `lines` of `{line_id, condition}`, one per line of the return. Works out the refund |
| GET | `/healthz` | Health check |

Reasons: `damaged_in_transit`, `wrong_item`, `defective`, `no_longer_needed`.
Conditions: `new`, `opened`, `damaged`. Statuses: `requested` → `received` → `inspected`, or `cancelled`.

## Events

| Stream | Event type | Direction |
|---|---|---|
| `returns.events` | `return.inspected` | Published after a return is inspected, with every line and its `restock` flag |

Consumes no events.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `RETURNS_DATABASE_PATH` | `returns.db` | SQLite database file |
| `ORDERS_URL` | `http://localhost:8002` | Orders service base URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis used for event streams |
| `RETURN_WINDOW_DAYS` | `30` | Days after shipping during which a return can be requested |
| `RESTOCKING_FEE_PERCENT` | `10` | Fee on opened items returned as `no_longer_needed` |
| `LOG_LEVEL` | `INFO` | Log level |

## Run

```bash
pip install "shop-platform @ git+https://github.com/sooraj-dube/shop-platform@v0.3.0"
pip install -e ".[dev]"
uvicorn returns.app:create_app --factory --port 8000
```

## Test

```bash
ruff check . && ruff format --check .
pytest
```

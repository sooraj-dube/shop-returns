CREATE TABLE returns (
    return_id TEXT PRIMARY KEY,
    rma_code TEXT NOT NULL UNIQUE,
    order_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    status TEXT NOT NULL,
    currency TEXT NOT NULL,
    refund_cents INTEGER,
    created_at TEXT NOT NULL,
    received_at TEXT,
    inspected_at TEXT
);

CREATE INDEX returns_order_id ON returns (order_id);

CREATE TABLE return_lines (
    return_id TEXT NOT NULL REFERENCES returns (return_id),
    line_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    reason TEXT NOT NULL,
    condition TEXT,
    restock INTEGER,
    refund_cents INTEGER,
    PRIMARY KEY (return_id, line_id)
);

CREATE TABLE idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    status_code INTEGER NOT NULL,
    response_body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

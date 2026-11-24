-- Sales Management System schema (SQLite)
-- Salespeople visit shops; each visit where they sell is one "order" with one or more items.

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    full_name     TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('sales', 'admin')),
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- A product is added once by the admin; it gets one variant per size (sizes are listed in app.py: SIZES).
-- Removing a product only sets active = 0 so past sales stay intact.
CREATE TABLE IF NOT EXISTS products (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_products_active_name
    ON products (name COLLATE NOCASE) WHERE active = 1;

CREATE TABLE IF NOT EXISTS product_variants (
    id         INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products (id),
    size       TEXT    NOT NULL,
    UNIQUE (product_id, size)
);

-- Shops (customers). Salespeople can add new shops from the field; admins can edit or remove them.
CREATE TABLE IF NOT EXISTS shops (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    area       TEXT    NOT NULL DEFAULT '',
    phone      TEXT    NOT NULL DEFAULT '',
    active     INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER REFERENCES users (id),
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_shops_active_name_area
    ON shops (name COLLATE NOCASE, area COLLATE NOCASE) WHERE active = 1;

-- One row per sale made at a shop (a salesperson can sell at many shops per day).
CREATE TABLE IF NOT EXISTS orders (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users (id),
    shop_id    INTEGER NOT NULL REFERENCES shops (id),
    sale_date  TEXT    NOT NULL,                     -- YYYY-MM-DD
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_orders_date ON orders (sale_date);
CREATE INDEX IF NOT EXISTS ix_orders_user_date ON orders (user_id, sale_date);
CREATE INDEX IF NOT EXISTS ix_orders_shop ON orders (shop_id);

-- What was sold in that sale. Price is entered by the salesperson, stored in paise (₹1 = 100).
CREATE TABLE IF NOT EXISTS order_items (
    id               INTEGER PRIMARY KEY,
    order_id         INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    variant_id       INTEGER NOT NULL REFERENCES product_variants (id),
    quantity         INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_paise INTEGER NOT NULL CHECK (unit_price_paise >= 0),
    UNIQUE (order_id, variant_id)
);
CREATE INDEX IF NOT EXISTS ix_order_items_variant ON order_items (variant_id);

-- Expenses salespeople record from the field (petrol, food, ...). Amount stored in paise.
CREATE TABLE IF NOT EXISTS expenses (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users (id),
    expense_date TEXT    NOT NULL,                   -- YYYY-MM-DD
    category     TEXT    NOT NULL,                   -- one of EXPENSE_CATEGORIES in app.py
    amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
    note         TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_expenses_user_date ON expenses (user_id, expense_date);
CREATE INDEX IF NOT EXISTS ix_expenses_date ON expenses (expense_date);

-- Expense categories, managed by the admin. Removing one only sets active = 0 (past expenses keep it).
CREATE TABLE IF NOT EXISTS expense_categories (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_expense_categories_active_name
    ON expense_categories (name COLLATE NOCASE) WHERE active = 1;

-- ===== Masala Parivaar (shop rewards) =====
-- Points are never stored as a balance: they are recomputed from orders, redemptions and adjustments (see rewards.py).
CREATE TABLE IF NOT EXISTS reward_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL                                -- JSON
);

CREATE TABLE IF NOT EXISTS gifts (
    id          INTEGER PRIMARY KEY,
    name_hi     TEXT    NOT NULL,
    name_en     TEXT    NOT NULL,
    points      INTEGER NOT NULL CHECK (points > 0),
    cost_paise  INTEGER NOT NULL DEFAULT 0,             -- admin-only, for the reward-cost report; never shown to shops
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS redemptions (
    id           INTEGER PRIMARY KEY,
    shop_id      INTEGER NOT NULL REFERENCES shops (id),
    kind         TEXT    NOT NULL CHECK (kind IN ('pack', 'gift')),
    size         TEXT,                                  -- pack: which size was given free
    product_id   INTEGER REFERENCES products (id),      -- pack: which flavour
    quantity     INTEGER NOT NULL DEFAULT 1,
    gift_id      INTEGER REFERENCES gifts (id),
    points       INTEGER NOT NULL CHECK (points > 0),
    status       TEXT    NOT NULL CHECK (status IN ('requested', 'approved', 'delivered', 'cancelled')),
    redeemed_on  TEXT    NOT NULL,                      -- YYYY-MM-DD
    created_by   INTEGER REFERENCES users (id),
    created_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_redemptions_shop ON redemptions (shop_id);

-- Manual corrections by the admin. Never edited or deleted: a mistake is fixed with another adjustment.
CREATE TABLE IF NOT EXISTS point_adjustments (
    id         INTEGER PRIMARY KEY,
    shop_id    INTEGER NOT NULL REFERENCES shops (id),
    points     INTEGER NOT NULL CHECK (points != 0),
    reason     TEXT    NOT NULL,
    adj_date   TEXT    NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users (id),
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_point_adjustments_shop ON point_adjustments (shop_id);

-- ===== Salesperson stock (the packs each salesperson carries) =====
-- Each salesperson sets their own full level ("limit") per product size. Stock left is never stored:
-- left = stock moves (start, refill, +1, -1) - packs sold in their sales - free packs they gave, counted from started_at.
CREATE TABLE IF NOT EXISTS stock_limits (
    user_id    INTEGER NOT NULL REFERENCES users (id),
    variant_id INTEGER NOT NULL REFERENCES product_variants (id),
    limit_qty  INTEGER NOT NULL CHECK (limit_qty >= 0),
    started_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,   -- sales/free packs from this moment count against stock
    PRIMARY KEY (user_id, variant_id)
);

CREATE TABLE IF NOT EXISTS stock_moves (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users (id),
    variant_id INTEGER NOT NULL REFERENCES product_variants (id),
    qty        INTEGER NOT NULL CHECK (qty != 0),
    kind       TEXT    NOT NULL CHECK (kind IN ('start', 'refill', 'add', 'remove')),
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_stock_moves_user ON stock_moves (user_id, variant_id);

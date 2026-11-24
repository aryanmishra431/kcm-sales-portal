"""Sales Management System — Flask backend with SQLite storage.

Salespeople visit shops and record each sale (shop + products + price + quantity).
Admins manage products, shops and users, and see reports.
"""
import csv
import io
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from functools import wraps
from urllib.parse import quote

from flask import Flask, Response, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

import rewards

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DATABASE", os.path.join(BASE_DIR, "sales.db"))
SECRET_FILE = os.path.join(BASE_DIR, ".secret_key")
# Product sizes. Every product automatically gets one variant per size.
# To add another size later, append it here and restart/reload the app.
SIZES = ("BIG", "SMALL", "20 BIG")


def _size_sums():
    """SQL that adds up quantity per size as columns u0, u1, ... (sizes are fixed constants above)."""
    return ",\n".join(
        f"COALESCE(SUM(CASE WHEN v.size = '{s}' THEN i.quantity END), 0) AS u{n}" for n, s in enumerate(SIZES)
    )


def units_of(row):
    return {s: (row[f"u{n}"] if row is not None else 0) for n, s in enumerate(SIZES)}
MIN_PASSWORD = 6
# Expense categories are managed by the admin on the Expenses page. These are only the starting list.
DEFAULT_EXPENSE_CATEGORIES = ("Petrol", "Food", "Travel", "Business Expense", "Other")
NOTE_REQUIRED_CATEGORY = "Other"  # expenses in this category must say what they were for
MAX_EXPENSE = 1_000_000  # rupees per entry
MAX_QTY = 1_000_000
MAX_PRICE = 10_000_000  # rupees
LEGACY_SHOP_NAME = "Old entries (before shop tracking)"


def _secret_key():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    if not os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(SECRET_FILE, 0o600)
    with open(SECRET_FILE) as f:
        return f.read().strip()


app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config.update(
    SECRET_KEY=_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",  # set to 1 when served over HTTPS
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)


# ============================================================================ database
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _migrate_daily_sales(db):
    """Version 1 stored one row per salesperson/product/day without a shop.
    Move those rows into orders under a placeholder shop, then keep the old table as sales_legacy."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sales'").fetchone():
        return
    rows = db.execute(
        """SELECT user_id, sale_date, variant_id, quantity, unit_price_paise FROM sales
           WHERE quantity > 0 ORDER BY user_id, sale_date, variant_id"""
    ).fetchall()
    if rows:
        shop_id = db.execute("INSERT INTO shops (name, area) VALUES (?, '')", (LEGACY_SHOP_NAME,)).lastrowid
        current, order_id = None, None
        for user_id, sale_date, variant_id, qty, price in rows:
            if (user_id, sale_date) != current:
                current = (user_id, sale_date)
                order_id = db.execute(
                    "INSERT INTO orders (user_id, shop_id, sale_date) VALUES (?, ?, ?)", (user_id, shop_id, sale_date)
                ).lastrowid
            db.execute(
                "INSERT INTO order_items (order_id, variant_id, quantity, unit_price_paise) VALUES (?, ?, ?, ?)",
                (order_id, variant_id, qty, price),
            )
        print(f"Migrated {len(rows)} old sales lines into shop '{LEGACY_SHOP_NAME}'.")
    db.execute("ALTER TABLE sales RENAME TO sales_legacy")


def _migrate_sizes(db):
    sql = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'product_variants'").fetchone()[0]
    if "CHECK" in sql:  # first versions only allowed BIG and SMALL; rebuild the table keeping the same ids
        db.execute("""CREATE TABLE product_variants_new (
                          id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products (id),
                          size TEXT NOT NULL, UNIQUE (product_id, size))""")
        db.execute("INSERT INTO product_variants_new (id, product_id, size) SELECT id, product_id, size FROM product_variants")
        db.execute("DROP TABLE product_variants")
        db.execute("ALTER TABLE product_variants_new RENAME TO product_variants")
    added = 0
    for size in SIZES:
        added += db.execute("INSERT OR IGNORE INTO product_variants (product_id, size) SELECT id, ? FROM products",
                            (size,)).rowcount
    if added:
        print(f"Added {added} product size variants ({', '.join(SIZES)}).")


def _seed_expense_categories(db):
    if db.execute("SELECT COUNT(*) FROM expense_categories").fetchone()[0]:
        return
    names = list(DEFAULT_EXPENSE_CATEGORIES)
    for (used,) in db.execute("SELECT DISTINCT category FROM expenses ORDER BY category"):
        if used.lower() not in (n.lower() for n in names):
            names.append(used)
    db.executemany("INSERT INTO expense_categories (name, sort_order) VALUES (?, ?)",
                   [(n, i) for i, n in enumerate(names)])


DEFAULT_GIFTS = [  # (points, Hindi, English)
    (30, "स्टील डिब्बा सेट", "Steel container set"),
    (50, "दीवार घड़ी", "Wall clock"),
    (100, "इलेक्ट्रिक केतली या प्रेशर कुकर", "Electric kettle or pressure cooker"),
    (150, "टेबल फैन या प्रेस", "Table fan or electric iron"),
    (200, "मिक्सर ग्राइंडर", "Mixer grinder"),
    (250, "इंडक्शन चूल्हा या सीलिंग फैन", "Induction cooktop or ceiling fan"),
    (500, "स्मार्टफोन", "Smartphone"),
    (1000, "32 इंच LED टीवी", "32-inch LED TV"),
]


def _seed_rewards(db):
    if not db.execute("SELECT 1 FROM reward_settings WHERE key = 'config'").fetchone():
        cfg = json.loads(json.dumps(rewards.DEFAULT_CONFIG))
        cfg["start_date"] = date.today().isoformat()   # only orders from launch day earn points
        db.execute("INSERT INTO reward_settings (key, value) VALUES ('config', ?)", (json.dumps(cfg),))
    if not db.execute("SELECT COUNT(*) FROM gifts").fetchone()[0]:
        db.executemany("INSERT INTO gifts (points, name_hi, name_en) VALUES (?, ?, ?)", DEFAULT_GIFTS)


def init_db():
    db = sqlite3.connect(DB_PATH)
    with open(os.path.join(BASE_DIR, "schema.sql")) as f:
        db.executescript(f.read())
    try:
        db.execute("BEGIN")
        _migrate_daily_sales(db)
        _migrate_sizes(db)
        _seed_expense_categories(db)
        _seed_rewards(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        password = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(9)
        db.execute(
            "INSERT INTO users (username, password_hash, full_name, role) VALUES (?, ?, ?, 'admin')",
            ("admin", generate_password_hash(password), "Administrator"),
        )
        print("=" * 60)
        print(" First run: created admin account")
        print("   username: admin")
        print(f"   password: {password}")
        print(" Log in and change this password from the Account page.")
        print("=" * 60)
    db.commit()
    db.close()


# ============================================================================ helpers
class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


@app.errorhandler(ApiError)
def _api_error(e):
    return jsonify(error=e.message), e.status


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Expected a JSON object")
    return data


def rupees(paise):
    return round((paise or 0) / 100, 2)


def today():
    return date.today()


def parse_date(value, field="date"):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ApiError(f"Invalid {field}; use YYYY-MM-DD")


def clean_text(value, field, max_len=80, required=True):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if required and not text:
        raise ApiError(f"{field} is required")
    if len(text) > max_len:
        raise ApiError(f"{field} must be at most {max_len} characters")
    return text


def check_password(pw):
    if not isinstance(pw, str) or len(pw) < MIN_PASSWORD:
        raise ApiError(f"Password must be at least {MIN_PASSWORD} characters")
    return pw


def opt_int(value, field):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(f"Invalid {field}")


def user_json(u):
    return {"id": u["id"], "username": u["username"], "full_name": u["full_name"], "role": u["role"]}


@app.before_request
def csrf_guard():
    # State-changing API calls must come from our own page (custom header cannot be sent cross-site without CORS).
    if request.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return jsonify(error="Bad request"), 400


def login_required(role=None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            uid = session.get("uid")
            u = uid and get_db().execute("SELECT * FROM users WHERE id = ? AND active = 1", (uid,)).fetchone()
            if not u:
                session.clear()
                return jsonify(error="Please log in"), 401
            if role and u["role"] != role:
                return jsonify(error="You do not have access to this"), 403
            g.user = u
            return fn(*args, **kwargs)
        return wrapper
    return deco


# ============================================================================ auth
_failed_logins = {}  # username -> [timestamps]; simple brute-force protection


@app.post("/api/login")
def login():
    d = body()
    username = str(d.get("username") or "").strip()
    password = str(d.get("password") or "")
    key, now = username.lower(), time.time()
    recent = [t for t in _failed_logins.get(key, []) if now - t < 900]
    _failed_logins[key] = recent
    if len(recent) >= 5:
        raise ApiError("Too many failed attempts. Try again in 15 minutes.", 429)
    u = get_db().execute("SELECT * FROM users WHERE username = ? AND active = 1", (username,)).fetchone()
    if not u or not check_password_hash(u["password_hash"], password):
        recent.append(now)
        raise ApiError("Invalid username or password", 401)
    _failed_logins.pop(key, None)
    session.clear()
    session.permanent = True
    session["uid"] = u["id"]
    return jsonify(user=user_json(u), **_app_config())


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
@login_required()
def me():
    return jsonify(user=user_json(g.user), **_app_config())


def _app_config():
    cfg = reward_config()
    return {"today": today().isoformat(), "sizes": SIZES, "expense_categories": expense_categories(),
            "note_required_category": NOTE_REQUIRED_CATEGORY,
            "rewards": {"earn": cfg["earn"], "pack_cost": cfg["pack_cost"], "start_date": cfg["start_date"]}}


def expense_categories():
    """Active category names, in display order ('Other' last)."""
    return [r[0] for r in get_db().execute(
        """SELECT name FROM expense_categories WHERE active = 1
           ORDER BY (name = ? COLLATE NOCASE), sort_order, name COLLATE NOCASE""", (NOTE_REQUIRED_CATEGORY,))]


@app.post("/api/me/password")
@login_required()
def change_my_password():
    d = body()
    if not check_password_hash(g.user["password_hash"], str(d.get("current") or "")):
        raise ApiError("Current password is incorrect")
    new = check_password(d.get("new"))
    db = get_db()
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new), g.user["id"]))
    db.commit()
    return jsonify(ok=True)


# ============================================================================ products & shops (shared)
def active_products():
    rows = get_db().execute(
        """SELECT p.id, p.name, v.id AS vid, v.size FROM products p
           JOIN product_variants v ON v.product_id = p.id
           WHERE p.active = 1 ORDER BY p.name COLLATE NOCASE, v.size"""
    ).fetchall()
    out = {}
    for r in rows:
        p = out.setdefault(r["id"], {"id": r["id"], "name": r["name"], "variants": {}})
        p["variants"][r["size"]] = r["vid"]
    return list(out.values())


@app.get("/api/products")
@login_required()
def list_products():
    return jsonify(products=active_products())


def shop_json(r):
    return {"id": r["id"], "name": r["name"], "area": r["area"], "phone": r["phone"]}


@app.get("/api/shops")
@login_required()
def list_shops():
    """All active shops. 'mine' = shops this user added or has sold to, shown first in the picker."""
    uid = g.user["id"]
    rows = get_db().execute(
        """SELECT s.id, s.name, s.area, s.phone,
                  (s.created_by = ? OR EXISTS (SELECT 1 FROM orders o WHERE o.shop_id = s.id AND o.user_id = ?)) AS mine,
                  (SELECT MAX(o.sale_date) FROM orders o WHERE o.shop_id = s.id AND o.user_id = ?) AS my_last_sale
           FROM shops s WHERE s.active = 1 ORDER BY s.name COLLATE NOCASE, s.area COLLATE NOCASE""",
        (uid, uid, uid),
    ).fetchall()
    return jsonify(shops=[{**shop_json(r), "mine": bool(r["mine"]), "my_last_sale": r["my_last_sale"]} for r in rows])


def _shop_fields(d):
    return (clean_text(d.get("name"), "Shop name"),
            clean_text(d.get("area"), "Area", required=False),
            clean_text(d.get("phone"), "Phone", max_len=20, required=False))


def _shop_taken(db, name, area, exclude_id=0):
    return db.execute(
        """SELECT 1 FROM shops WHERE active = 1 AND name = ? COLLATE NOCASE AND area = ? COLLATE NOCASE AND id != ?""",
        (name, area, exclude_id),
    ).fetchone()


@app.post("/api/shops")
@login_required()
def add_shop():
    """Salespeople (and admins) can add a shop on the spot."""
    name, area, phone = _shop_fields(body())
    db = get_db()
    if _shop_taken(db, name, area):
        raise ApiError("This shop already exists" + (f" in {area}" if area else "") + ". Search for it instead.")
    with db:
        sid = db.execute("INSERT INTO shops (name, area, phone, created_by) VALUES (?, ?, ?, ?)",
                         (name, area, phone, g.user["id"])).lastrowid
    return jsonify(shop={"id": sid, "name": name, "area": area, "phone": phone}), 201


# ============================================================================ salesperson: sales at shops
def load_orders(where, params, limit=None):
    """Return orders (with shop, salesperson and items) matching a WHERE clause on alias o."""
    db = get_db()
    orders = db.execute(
        f"""SELECT o.id, o.sale_date, o.created_at, o.updated_at, o.user_id, u.full_name AS user_name,
                   s.id AS shop_id, s.name AS shop_name, s.area AS shop_area, s.phone AS shop_phone
            FROM orders o JOIN shops s ON s.id = o.shop_id JOIN users u ON u.id = o.user_id
            WHERE {where} ORDER BY o.sale_date DESC, o.id DESC {'LIMIT ' + str(int(limit)) if limit else ''}""",
        params,
    ).fetchall()
    if not orders:
        return []
    ids = [o["id"] for o in orders]
    items = {}
    for i in db.execute(
        f"""SELECT i.order_id, i.variant_id, i.quantity, i.unit_price_paise, v.size, p.id AS product_id, p.name, p.active
            FROM order_items i JOIN product_variants v ON v.id = i.variant_id JOIN products p ON p.id = v.product_id
            WHERE i.order_id IN ({','.join('?' * len(ids))}) ORDER BY p.name COLLATE NOCASE, v.size""",
        ids,
    ):
        items.setdefault(i["order_id"], []).append({
            "variant_id": i["variant_id"], "product_id": i["product_id"], "product": i["name"],
            "product_active": bool(i["active"]), "size": i["size"],
            "qty": i["quantity"], "price": rupees(i["unit_price_paise"]),
            "amount": rupees(i["quantity"] * i["unit_price_paise"]),
        })
    out = []
    for o in orders:
        its = items.get(o["id"], [])
        out.append({
            "id": o["id"], "date": o["sale_date"], "created_at": o["created_at"], "updated_at": o["updated_at"],
            "user_id": o["user_id"], "user_name": o["user_name"],
            "shop": {"id": o["shop_id"], "name": o["shop_name"], "area": o["shop_area"], "phone": o["shop_phone"]},
            "items": its,
            "units": {s: sum(i["qty"] for i in its if i["size"] == s) for s in SIZES},
            "total": round(sum(i["amount"] for i in its), 2),
        })
    return out


@app.get("/api/my/orders")
@login_required("sales")
def my_orders():
    d = parse_date(request.args.get("date", today().isoformat()))
    return jsonify(date=d.isoformat(), orders=load_orders("o.user_id = ? AND o.sale_date = ?", [g.user["id"], d.isoformat()]))


@app.get("/api/my/prices")
@login_required("sales")
def my_last_prices():
    """Last price this salesperson charged for each product size — at this shop if they sold there before,
    otherwise anywhere. Used to pre-fill the price boxes."""
    shop_id = opt_int(request.args.get("shop_id"), "shop")
    q = """SELECT variant_id, unit_price_paise FROM (
             SELECT i.variant_id, i.unit_price_paise,
                    ROW_NUMBER() OVER (PARTITION BY i.variant_id ORDER BY o.sale_date DESC, o.id DESC) AS rn
             FROM order_items i JOIN orders o ON o.id = i.order_id
             WHERE o.user_id = ? AND i.unit_price_paise > 0 {extra}) WHERE rn = 1"""
    db = get_db()
    prices = {r[0]: rupees(r[1]) for r in db.execute(q.format(extra=""), (g.user["id"],))}
    if shop_id:
        prices.update({r[0]: rupees(r[1]) for r in db.execute(q.format(extra="AND o.shop_id = ?"), (g.user["id"], shop_id))})
    return jsonify(prices=prices)


def _validate_order(d, existing=None):
    db = get_db()
    sale_date = parse_date(d.get("date"))
    if sale_date > today():
        raise ApiError("You cannot enter sales for a future date")
    shop_id = opt_int(d.get("shop_id"), "shop")
    if not shop_id:
        raise ApiError("Choose the shop")
    shop = db.execute("SELECT id, active FROM shops WHERE id = ?", (shop_id,)).fetchone()
    if not shop or (not shop["active"] and not (existing and existing["shop_id"] == shop_id)):
        raise ApiError("That shop no longer exists. Choose another shop.")

    items = d.get("items")
    if not isinstance(items, list):
        raise ApiError("No products were sent")
    allowed = {
        r["id"]: f'{r["name"]} ({r["size"]})'
        for r in db.execute(
            """SELECT v.id, v.size, p.name FROM product_variants v JOIN products p ON p.id = v.product_id
               WHERE p.active = 1"""
        )
    }
    if existing:  # products removed after this sale was entered can still be kept on it
        for r in db.execute(
            """SELECT v.id, v.size, p.name FROM order_items i JOIN product_variants v ON v.id = i.variant_id
               JOIN products p ON p.id = v.product_id WHERE i.order_id = ?""", (existing["id"],)
        ):
            allowed.setdefault(r["id"], f'{r["name"]} ({r["size"]})')

    clean, seen, missing_price = [], set(), []
    for it in items:
        try:
            vid = int(it["variant_id"])
            qty = int(it.get("qty") or 0)
            price = float(it.get("price") or 0)
        except (KeyError, TypeError, ValueError):
            raise ApiError("Quantities and prices must be numbers")
        if vid not in allowed:
            raise ApiError("One of the products no longer exists. Reload the page.")
        if vid in seen:
            raise ApiError(f"{allowed[vid]} is listed twice")
        seen.add(vid)
        if not (0 <= qty <= MAX_QTY) or not (0 <= price <= MAX_PRICE):
            raise ApiError(f"Invalid quantity or price for {allowed[vid]}")
        if qty == 0:
            continue
        if price <= 0:
            missing_price.append(allowed[vid])
        clean.append((vid, qty, round(price * 100)))
    if missing_price:
        raise ApiError("Enter a price for: " + ", ".join(missing_price))
    if not clean:
        raise ApiError("Enter the quantity for at least one product")
    return sale_date.isoformat(), shop_id, clean


def _own_order(order_id):
    o = get_db().execute("SELECT * FROM orders WHERE id = ? AND user_id = ?", (order_id, g.user["id"])).fetchone()
    if not o:
        raise ApiError("Sale not found", 404)
    return o


def _write_items(db, order_id, items):
    db.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
    db.executemany(
        "INSERT INTO order_items (order_id, variant_id, quantity, unit_price_paise) VALUES (?, ?, ?, ?)",
        [(order_id, vid, q, p) for vid, q, p in items],
    )


@app.post("/api/my/orders")
@login_required("sales")
def create_order():
    sale_date, shop_id, items = _validate_order(body())
    db = get_db()
    with db:
        oid = db.execute("INSERT INTO orders (user_id, shop_id, sale_date) VALUES (?, ?, ?)",
                         (g.user["id"], shop_id, sale_date)).lastrowid
        _write_items(db, oid, items)
    return jsonify(ok=True, id=oid, shop_id=shop_id, points=order_earned_points(oid)), 201


@app.put("/api/my/orders/<int:order_id>")
@login_required("sales")
def update_order(order_id):
    existing = _own_order(order_id)
    sale_date, shop_id, items = _validate_order(body(), existing)
    db = get_db()
    with db:
        db.execute("UPDATE orders SET shop_id = ?, sale_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                   (shop_id, sale_date, order_id))
        _write_items(db, order_id, items)
        ensure_points_ok({existing["shop_id"], shop_id})
    return jsonify(ok=True, id=order_id, shop_id=shop_id, points=order_earned_points(order_id))


@app.delete("/api/my/orders/<int:order_id>")
@login_required("sales")
def delete_order(order_id):
    existing = _own_order(order_id)
    db = get_db()
    with db:
        db.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
        db.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        ensure_points_ok({existing["shop_id"]})
    return jsonify(ok=True)


@app.get("/api/my/history")
@login_required("sales")
def history():
    rows = get_db().execute(
        f"""SELECT o.sale_date, COUNT(DISTINCT o.id) AS sales, COUNT(DISTINCT o.shop_id) AS shops,
                  {_size_sums()},
                  SUM(i.quantity * i.unit_price_paise) AS amount
           FROM orders o JOIN order_items i ON i.order_id = o.id JOIN product_variants v ON v.id = i.variant_id
           WHERE o.user_id = ? GROUP BY o.sale_date ORDER BY o.sale_date DESC LIMIT 366""",
        (g.user["id"],),
    ).fetchall()
    days = {r["sale_date"]: {"date": r["sale_date"], "sales": r["sales"], "shops": r["shops"],
                             "units": units_of(r), "amount": rupees(r["amount"]), "expenses": 0} for r in rows}
    for r in get_db().execute(
        """SELECT expense_date, SUM(amount_paise) AS total FROM expenses WHERE user_id = ?
           GROUP BY expense_date ORDER BY expense_date DESC LIMIT 366""", (g.user["id"],)
    ):
        d = days.setdefault(r["expense_date"], {"date": r["expense_date"], "sales": 0, "shops": 0,
                                                "units": units_of(None), "amount": 0, "expenses": 0})
        d["expenses"] = rupees(r["total"])
    return jsonify(days=sorted(days.values(), key=lambda d: d["date"], reverse=True)[:366])


# ============================================================================ salesperson: monthly dashboard
def _month_bounds(month_text):
    try:
        y, m = (int(x) for x in str(month_text).split("-"))
        first = date(y, m, 1)
    except (TypeError, ValueError):
        raise ApiError("Invalid month; use YYYY-MM")
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return first, nxt - timedelta(days=1)


def work_settings():
    row = get_db().execute("SELECT value FROM reward_settings WHERE key = 'work'").fetchone()
    cfg = {"sundays_off": False}   # Sundays are working days unless the admin switches this on
    if row:
        cfg.update(json.loads(row[0]))
    return cfg


def is_day_off(d, cfg):
    return bool(cfg.get("sundays_off")) and d.weekday() == 6


def zero_sale_days(sale_dates, start, end, cfg):
    """Working days between start and end (inclusive) with no sale. Days off (Sundays) never count."""
    out, d = [], start
    while d <= end:
        if d.isoformat() not in sale_dates and not is_day_off(d, cfg):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


@app.get("/api/my/dashboard")
@login_required("sales")
def my_dashboard():
    return jsonify(month_dashboard(g.user["id"], request.args.get("month")))


@app.get("/api/admin/team-calendar")
@login_required("admin")
def team_calendar():
    month = request.args.get("month")
    people = get_db().execute(
        "SELECT id, full_name FROM users WHERE role = 'sales' AND active = 1 ORDER BY full_name COLLATE NOCASE").fetchall()
    return jsonify(month=month, work=work_settings(),
                   people=[{"id": u["id"], "name": u["full_name"], **month_dashboard(u["id"], month)} for u in people])


@app.put("/api/admin/work-settings")
@login_required("admin")
def update_work_settings():
    cfg = work_settings()
    cfg["sundays_off"] = bool(body().get("sundays_off"))
    db = get_db()
    with db:
        db.execute("INSERT OR REPLACE INTO reward_settings (key, value) VALUES ('work', ?)", (json.dumps(cfg),))
    return jsonify(ok=True, work=cfg)


def month_dashboard(uid, month_text):
    now = today()
    first, last = _month_bounds(month_text or now.strftime("%Y-%m"))
    if first > now:
        raise ApiError("That month hasn't started yet")
    upto = min(last, now)                      # days that have happened so far
    db = get_db()
    daily = {r["sale_date"]: r for r in db.execute(
        """SELECT o.sale_date, COUNT(DISTINCT o.id) AS orders, COUNT(DISTINCT o.shop_id) AS shops,
                  SUM(i.quantity * i.unit_price_paise) AS revenue
           FROM orders o JOIN order_items i ON i.order_id = o.id
           WHERE o.user_id = ? AND o.sale_date BETWEEN ? AND ? GROUP BY o.sale_date""",
        (uid, first.isoformat(), last.isoformat()))}
    tot = db.execute(
        f"""SELECT COUNT(DISTINCT o.id) AS orders, COUNT(DISTINCT o.shop_id) AS shops, {SUMS}
            {ITEMS_JOIN} WHERE o.user_id = ? AND o.sale_date BETWEEN ? AND ?""",
        (uid, first.isoformat(), last.isoformat())).fetchone()
    expenses = db.execute("SELECT COALESCE(SUM(amount_paise), 0) FROM expenses WHERE user_id = ? AND expense_date BETWEEN ? AND ?",
                          (uid, first.isoformat(), last.isoformat())).fetchone()[0]
    # same period last month (1st .. same day number), for comparison
    prev_last = first - timedelta(days=1)
    prev_first = prev_last.replace(day=1)
    prev_upto = prev_first.replace(day=min(upto.day, prev_last.day))
    prev = db.execute("""SELECT COALESCE(SUM(i.quantity * i.unit_price_paise), 0) FROM orders o JOIN order_items i ON i.order_id = o.id
                         WHERE o.user_id = ? AND o.sale_date BETWEEN ? AND ?""",
                      (uid, prev_first.isoformat(), prev_upto.isoformat())).fetchone()[0]
    work = work_settings()
    days, zero, working = [], [], 0
    d = first
    while d <= last:
        r = daily.get(d.isoformat())
        status = "future" if d > now else "sale" if r else "off" if is_day_off(d, work) else "zero"
        if status == "zero":
            zero.append(d.isoformat())
        if d <= now and (r or not is_day_off(d, work)):
            working += 1
        days.append({"date": d.isoformat(), "day": d.day, "weekday": d.weekday(), "status": status,
                     "revenue": rupees(r["revenue"]) if r else 0, "orders": r["orders"] if r else 0})
        d += timedelta(days=1)
    worked = sum(1 for x in days if x["status"] == "sale")
    best = max((x for x in days if x["status"] == "sale"), key=lambda x: x["revenue"], default=None)
    revenue = rupees(tot["revenue"])
    return dict(
        month=first.strftime("%Y-%m"), first=first.isoformat(), last=last.isoformat(), upto=upto.isoformat(),
        days_so_far=(upto - first).days + 1, working_days=working, days_worked=worked, zero_days=zero,
        sundays_off=work["sundays_off"],
        revenue=revenue, orders=tot["orders"], shops=tot["shops"], units=units_of(tot), expenses=rupees(expenses),
        avg_per_day_worked=round(revenue / worked) if worked else 0,
        prev_revenue=rupees(prev), prev_period=[prev_first.isoformat(), prev_upto.isoformat()],
        best_day=best, days=days,
    )


# ============================================================================ salesperson: expenses
def expense_json(r):
    return {"id": r["id"], "date": r["expense_date"], "category": r["category"], "amount": rupees(r["amount_paise"]),
            "note": r["note"], "user_id": r["user_id"], "user_name": r["full_name"] if "full_name" in r.keys() else None,
            "created_at": r["created_at"]}


def _validate_expense(d, existing=None):
    exp_date = parse_date(d.get("date"))
    if exp_date > today():
        raise ApiError("You cannot enter expenses for a future date")
    category = d.get("category")
    allowed = expense_categories()
    if category not in allowed and not (existing and existing["category"] == category):
        raise ApiError("Choose a category: " + ", ".join(allowed))
    try:
        amount = float(d.get("amount") or 0)
    except (TypeError, ValueError):
        raise ApiError("Amount must be a number")
    if not (0 < amount <= MAX_EXPENSE):
        raise ApiError("Enter the amount spent")
    note = clean_text(d.get("note"), "Note", max_len=200, required=False)
    if category.lower() == NOTE_REQUIRED_CATEGORY.lower() and not note:
        raise ApiError(f"For '{category}', write what the money was spent on")
    return exp_date.isoformat(), category, round(amount * 100), note


def _own_expense(expense_id):
    e = get_db().execute("SELECT * FROM expenses WHERE id = ? AND user_id = ?", (expense_id, g.user["id"])).fetchone()
    if not e:
        raise ApiError("Expense not found", 404)
    return e


@app.get("/api/my/expenses")
@login_required("sales")
def my_expenses():
    d = parse_date(request.args.get("date", today().isoformat()))
    rows = get_db().execute(
        "SELECT * FROM expenses WHERE user_id = ? AND expense_date = ? ORDER BY id DESC", (g.user["id"], d.isoformat())
    ).fetchall()
    return jsonify(date=d.isoformat(), expenses=[expense_json(r) for r in rows], categories=expense_categories())


@app.post("/api/my/expenses")
@login_required("sales")
def add_expense():
    exp_date, category, paise, note = _validate_expense(body())
    db = get_db()
    with db:
        eid = db.execute("INSERT INTO expenses (user_id, expense_date, category, amount_paise, note) VALUES (?, ?, ?, ?, ?)",
                         (g.user["id"], exp_date, category, paise, note)).lastrowid
    return jsonify(ok=True, id=eid), 201


@app.put("/api/my/expenses/<int:expense_id>")
@login_required("sales")
def update_expense(expense_id):
    existing = _own_expense(expense_id)
    exp_date, category, paise, note = _validate_expense(body(), existing)
    db = get_db()
    with db:
        db.execute("""UPDATE expenses SET expense_date = ?, category = ?, amount_paise = ?, note = ?,
                      updated_at = CURRENT_TIMESTAMP WHERE id = ?""", (exp_date, category, paise, note, expense_id))
    return jsonify(ok=True)


@app.delete("/api/my/expenses/<int:expense_id>")
@login_required("sales")
def delete_expense(expense_id):
    _own_expense(expense_id)
    db = get_db()
    with db:
        db.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    return jsonify(ok=True)


# ============================================================================ admin: reports
def report_filters():
    t = parse_date(request.args.get("to") or today().isoformat(), "to date")
    f = parse_date(request.args.get("from") or (t - timedelta(days=6)).isoformat(), "from date")
    if f > t:
        raise ApiError("'From' date must be before 'To' date")
    uid = opt_int(request.args.get("user_id"), "salesperson")
    sid = opt_int(request.args.get("shop_id"), "shop")
    where, params = "o.sale_date BETWEEN ? AND ?", [f.isoformat(), t.isoformat()]
    if uid:
        where += " AND o.user_id = ?"; params.append(uid)
    if sid:
        where += " AND o.shop_id = ?"; params.append(sid)
    return f, t, uid, sid, where, params


ITEMS_JOIN = """FROM orders o JOIN order_items i ON i.order_id = o.id
                JOIN product_variants v ON v.id = i.variant_id"""
SUMS = _size_sums() + ",\nCOALESCE(SUM(i.quantity * i.unit_price_paise), 0) AS revenue"


@app.get("/api/admin/report")
@login_required("admin")
def report():
    f, t, uid, sid, where, params = report_filters()
    db = get_db()

    tot = db.execute(f"SELECT {SUMS} {ITEMS_JOIN} WHERE {where}", params).fetchone()
    counts = db.execute(f"SELECT COUNT(*) AS sales, COUNT(DISTINCT o.shop_id) AS shops FROM orders o WHERE {where}",
                        params).fetchone()

    # --- by product
    empty = lambda: {s: {"qty": 0, "revenue": 0} for s in SIZES}  # noqa: E731
    products = {p["id"]: {"name": p["name"], **empty()} for p in active_products()}
    for r in db.execute(
        f"""SELECT p.id, p.name, p.active, v.size, SUM(i.quantity) AS qty, SUM(i.quantity * i.unit_price_paise) AS revenue
            {ITEMS_JOIN} JOIN products p ON p.id = v.product_id WHERE {where} GROUP BY p.id, v.size""", params
    ):
        p = products.setdefault(r["id"], {"name": r["name"] + ("" if r["active"] else " (removed)"), **empty()})
        if r["size"] in SIZES:
            p[r["size"]] = {"qty": r["qty"], "revenue": r["revenue"]}
    product_list = []
    for p in products.values():
        row = {"name": p["name"]}
        for size in SIZES:
            q, rev = p[size]["qty"], p[size]["revenue"]
            row[size] = {"qty": q, "revenue": rupees(rev), "avg_price": rupees(rev / q) if q else None}
        product_list.append(row)
    product_list.sort(key=lambda r: -sum(r[s]["revenue"] for s in SIZES))

    # --- by salesperson
    sales_users = db.execute(
        "SELECT id, full_name, active FROM users WHERE role = 'sales' ORDER BY full_name COLLATE NOCASE"
    ).fetchall()
    item_sums = {r["user_id"]: r for r in db.execute(f"SELECT o.user_id, {SUMS} {ITEMS_JOIN} WHERE {where} GROUP BY o.user_id", params)}
    order_sums = {r["user_id"]: r for r in db.execute(
        f"""SELECT o.user_id, COUNT(*) AS sales, COUNT(DISTINCT o.shop_id) AS shops, COUNT(DISTINCT o.sale_date) AS days
            FROM orders o WHERE {where} GROUP BY o.user_id""", params)}
    work = work_settings()
    sale_dates = {}
    for r in db.execute("SELECT DISTINCT user_id, sale_date FROM orders WHERE sale_date BETWEEN ? AND ?", (f.isoformat(), t.isoformat())):
        sale_dates.setdefault(r[0], set()).add(r[1])
    exp_where, exp_params = "e.expense_date BETWEEN ? AND ?", [f.isoformat(), t.isoformat()]
    if uid:
        exp_where += " AND e.user_id = ?"; exp_params.append(uid)
    exp_sums = {r["user_id"]: r["total"] for r in db.execute(
        f"SELECT e.user_id, SUM(e.amount_paise) AS total FROM expenses e WHERE {exp_where} GROUP BY e.user_id", exp_params)}
    users_out = []
    for u in sales_users:
        if uid and u["id"] != uid:
            continue
        isum, osum = item_sums.get(u["id"]), order_sums.get(u["id"])
        if not u["active"] and not osum and not exp_sums.get(u["id"]):
            continue
        users_out.append({"id": u["id"], "name": u["full_name"],
                          "days": osum["days"] if osum else 0, "sales": osum["sales"] if osum else 0,
                          "shops": osum["shops"] if osum else 0,
                          "units": units_of(isum),
                          "revenue": rupees(isum["revenue"]) if isum else 0,
                          "expenses": rupees(exp_sums.get(u["id"], 0)),
                          "zero_days": len(zero_sale_days(sale_dates.get(u["id"], set()), f, min(t, today()), work))})

    # --- by shop
    shop_rows = db.execute(
        f"""SELECT s.id, s.name, s.area, COUNT(DISTINCT o.id) AS visits, MAX(o.sale_date) AS last_sale, {SUMS}
            {ITEMS_JOIN} JOIN shops s ON s.id = o.shop_id WHERE {where}
            GROUP BY s.id ORDER BY revenue DESC LIMIT 200""", params
    ).fetchall()

    by_day = db.execute(
        f"SELECT o.sale_date, {SUMS} {ITEMS_JOIN} WHERE {where} GROUP BY o.sale_date ORDER BY o.sale_date", params
    ).fetchall()

    missing = db.execute(
        """SELECT full_name FROM users u WHERE role = 'sales' AND active = 1
           AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.id AND o.sale_date = ?)
           ORDER BY full_name COLLATE NOCASE""",
        (today().isoformat(),),
    ).fetchall()

    shops_all = db.execute("SELECT id, name, area FROM shops WHERE active = 1 ORDER BY name COLLATE NOCASE").fetchall()

    return jsonify(
        from_date=f.isoformat(), to_date=t.isoformat(),
        totals={"revenue": rupees(tot["revenue"]), "units": units_of(tot),
                "sales": counts["sales"], "shops": counts["shops"], "expenses": rupees(sum(exp_sums.values()))},
        products=product_list, users=users_out,
        shops=[{"id": r["id"], "name": r["name"], "area": r["area"], "visits": r["visits"], "last_sale": r["last_sale"],
                "units": units_of(r), "revenue": rupees(r["revenue"])} for r in shop_rows],
        by_day=[{"date": r["sale_date"], "revenue": rupees(r["revenue"])} for r in by_day],
        recent=load_orders(where, params, limit=200),
        missing_today=[r["full_name"] for r in missing],
        salespeople=[{"id": u["id"], "name": u["full_name"]} for u in sales_users],
        shop_options=[{"id": s["id"], "name": s["name"], "area": s["area"]} for s in shops_all],
    )


def _csv_safe(v):
    s = str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@app.get("/api/admin/export.csv")
@login_required("admin")
def export_csv():
    f, t, _uid, _sid, where, params = report_filters()
    rows = get_db().execute(
        f"""SELECT o.id, o.sale_date, u.full_name, s.name AS shop, s.area, p.name AS product, v.size,
                   i.quantity, i.unit_price_paise
            {ITEMS_JOIN} JOIN users u ON u.id = o.user_id JOIN shops s ON s.id = o.shop_id
            JOIN products p ON p.id = v.product_id
            WHERE {where} ORDER BY o.sale_date, u.full_name, o.id, p.name, v.size""",
        params,
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "sale_no", "salesperson", "shop", "area", "product", "size", "quantity", "unit_price", "amount"])
    for r in rows:
        w.writerow([r["sale_date"], r["id"], _csv_safe(r["full_name"]), _csv_safe(r["shop"]), _csv_safe(r["area"]),
                    _csv_safe(r["product"]), r["size"], r["quantity"], rupees(r["unit_price_paise"]),
                    rupees(r["quantity"] * r["unit_price_paise"])])
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=sales_{f}_to_{t}.csv"})


# ============================================================================ admin: expenses
def expense_filters():
    t = parse_date(request.args.get("to") or today().isoformat(), "to date")
    f = parse_date(request.args.get("from") or (t - timedelta(days=6)).isoformat(), "from date")
    if f > t:
        raise ApiError("'From' date must be before 'To' date")
    where, params = "e.expense_date BETWEEN ? AND ?", [f.isoformat(), t.isoformat()]
    uid = opt_int(request.args.get("user_id"), "salesperson")
    if uid:
        where += " AND e.user_id = ?"; params.append(uid)
    cat = request.args.get("category") or None
    if cat:
        where += " AND e.category = ?"; params.append(cat)
    return f, t, where, params


@app.get("/api/admin/expenses")
@login_required("admin")
def admin_expenses():
    f, t, where, params = expense_filters()
    db = get_db()
    by_cat = {r["category"]: r["total"] for r in db.execute(
        f"SELECT e.category, SUM(e.amount_paise) AS total FROM expenses e WHERE {where} GROUP BY e.category", params)}
    cats = expense_categories()
    cats += sorted(c for c in by_cat if c not in cats)  # removed categories that still have expenses in this period
    per_user = {}
    for r in db.execute(
        f"""SELECT e.user_id, u.full_name, e.category, SUM(e.amount_paise) AS total, COUNT(DISTINCT e.expense_date) AS days
            FROM expenses e JOIN users u ON u.id = e.user_id WHERE {where} GROUP BY e.user_id, e.category""", params
    ):
        p = per_user.setdefault(r["user_id"], {"id": r["user_id"], "name": r["full_name"],
                                               "by_category": {c: 0 for c in cats}, "total": 0})
        p["by_category"][r["category"]] = p["by_category"].get(r["category"], 0) + rupees(r["total"])
        p["total"] = round(p["total"] + rupees(r["total"]), 2)
    rows = db.execute(
        f"""SELECT e.*, u.full_name FROM expenses e JOIN users u ON u.id = e.user_id WHERE {where}
            ORDER BY e.expense_date DESC, e.id DESC LIMIT 500""", params
    ).fetchall()
    salespeople = db.execute("SELECT id, full_name FROM users WHERE role = 'sales' ORDER BY full_name COLLATE NOCASE").fetchall()
    return jsonify(
        from_date=f.isoformat(), to_date=t.isoformat(),
        total=rupees(sum(by_cat.values())),
        categories=cats,
        by_category={c: rupees(by_cat.get(c, 0)) for c in cats},
        users=sorted(per_user.values(), key=lambda p: -p["total"]),
        expenses=[expense_json(r) for r in rows],
        salespeople=[{"id": u["id"], "name": u["full_name"]} for u in salespeople],
    )


@app.get("/api/admin/expense-categories")
@login_required("admin")
def admin_expense_categories():
    rows = get_db().execute(
        """SELECT c.id, c.name, (SELECT COUNT(*) FROM expenses e WHERE e.category = c.name) AS uses
           FROM expense_categories c WHERE c.active = 1
           ORDER BY (c.name = ? COLLATE NOCASE), c.sort_order, c.name COLLATE NOCASE""", (NOTE_REQUIRED_CATEGORY,)
    ).fetchall()
    return jsonify(categories=[dict(r) for r in rows])


def _category_taken(db, name, exclude_id=0):
    return db.execute("SELECT 1 FROM expense_categories WHERE active = 1 AND name = ? COLLATE NOCASE AND id != ?",
                      (name, exclude_id)).fetchone()


@app.post("/api/admin/expense-categories")
@login_required("admin")
def add_expense_category():
    name = clean_text(body().get("name"), "Category name", max_len=40)
    db = get_db()
    if _category_taken(db, name):
        raise ApiError("That category already exists")
    with db:
        nxt = db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM expense_categories").fetchone()[0]
        db.execute("INSERT INTO expense_categories (name, sort_order) VALUES (?, ?)", (name, nxt))
    return jsonify(ok=True), 201


@app.patch("/api/admin/expense-categories/<int:cid>")
@login_required("admin")
def rename_expense_category(cid):
    name = clean_text(body().get("name"), "Category name", max_len=40)
    db = get_db()
    row = db.execute("SELECT name FROM expense_categories WHERE id = ? AND active = 1", (cid,)).fetchone()
    if not row:
        raise ApiError("Category not found", 404)
    if _category_taken(db, name, cid):
        raise ApiError("That category already exists")
    with db:  # past expenses move to the new name too
        db.execute("UPDATE expense_categories SET name = ? WHERE id = ?", (name, cid))
        db.execute("UPDATE expenses SET category = ? WHERE category = ?", (name, row["name"]))
    return jsonify(ok=True)


@app.delete("/api/admin/expense-categories/<int:cid>")
@login_required("admin")
def remove_expense_category(cid):
    db = get_db()
    if db.execute("SELECT COUNT(*) FROM expense_categories WHERE active = 1").fetchone()[0] <= 1:
        raise ApiError("Keep at least one category")
    with db:
        cur = db.execute("UPDATE expense_categories SET active = 0 WHERE id = ? AND active = 1", (cid,))
    if cur.rowcount == 0:
        raise ApiError("Category not found", 404)
    return jsonify(ok=True)


@app.get("/api/admin/expenses.csv")
@login_required("admin")
def export_expenses_csv():
    f, t, where, params = expense_filters()
    rows = get_db().execute(
        f"""SELECT e.*, u.full_name, u.username FROM expenses e JOIN users u ON u.id = e.user_id WHERE {where}
            ORDER BY e.expense_date, u.full_name, e.id""", params
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "salesperson", "username", "category", "amount", "note"])
    for r in rows:
        w.writerow([r["expense_date"], _csv_safe(r["full_name"]), _csv_safe(r["username"]), r["category"],
                    rupees(r["amount_paise"]), _csv_safe(r["note"])])
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=expenses_{f}_to_{t}.csv"})


# ============================================================================ Masala Parivaar rewards
def reward_config():
    row = get_db().execute("SELECT value FROM reward_settings WHERE key = 'config'").fetchone()
    cfg = json.loads(json.dumps(rewards.DEFAULT_CONFIG))
    if row:
        stored = json.loads(row[0])
        for k, v in stored.items():
            cfg[k] = {**cfg[k], **v} if isinstance(cfg.get(k), dict) and isinstance(v, dict) else v
    cfg["start_date"] = cfg.get("start_date") or today().isoformat()
    return cfg


def _shop_events(shop_id, cfg):
    db = get_db()
    events, orders = [], {}
    for r in db.execute(
        """SELECT o.id, o.sale_date, v.size, SUM(i.quantity) AS qty
           FROM orders o JOIN order_items i ON i.order_id = o.id JOIN product_variants v ON v.id = i.variant_id
           WHERE o.shop_id = ? AND o.sale_date >= ? GROUP BY o.id, v.size ORDER BY o.id""",
        (shop_id, cfg["start_date"]),
    ):
        o = orders.setdefault(r["id"], {"date": r["sale_date"], "lines": []})
        o["lines"].append((r["size"], r["qty"]))
    for oid, o in orders.items():
        pts = rewards.order_points(o["lines"], cfg)
        if pts > 0:
            label = "Sale: " + ", ".join(f"{q} {s}" for s, q in o["lines"])
            events.append({"date": o["date"], "kind": "earn", "points": pts, "seq": oid, "ref": f"order:{oid}", "label": label})
    for r in db.execute(
        """SELECT r.*, g.name_en, g.name_hi, p.name AS product FROM redemptions r
           LEFT JOIN gifts g ON g.id = r.gift_id LEFT JOIN products p ON p.id = r.product_id
           WHERE r.shop_id = ? AND r.status != 'cancelled'""", (shop_id,)
    ):
        label = (f"Free {r['quantity']} × {r['size']}" + (f" ({r['product']})" if r["product"] else "")
                 if r["kind"] == "pack" else f"Gift: {r['name_en']} — {r['status']}")
        events.append({"date": r["redeemed_on"], "kind": "redeem", "points": r["points"], "seq": r["id"],
                       "ref": f"redemption:{r['id']}", "label": label})
    for r in db.execute("SELECT * FROM point_adjustments WHERE shop_id = ?", (shop_id,)):
        events.append({"date": r["adj_date"], "kind": "adjust", "points": r["points"], "seq": r["id"],
                       "ref": f"adjust:{r['id']}", "label": "Adjustment: " + r["reason"]})
    return events


def shop_points(shop_id, cfg=None):
    cfg = cfg or reward_config()
    return rewards.compute(_shop_events(shop_id, cfg), cfg, today())


def ensure_points_ok(shop_ids, cfg=None):
    """Call inside a transaction after a change: refuse it if any shop's points would go negative."""
    for sid in shop_ids:
        if sid and not shop_points(sid, cfg)["ok"]:
            name = get_db().execute("SELECT name FROM shops WHERE id = ?", (sid,)).fetchone()
            raise ApiError(f"This change would make the Masala Parivaar points of {name[0] if name else 'the shop'} "
                           "negative, because points were already used for a free pack or gift. Ask the admin.")


def order_earned_points(order_id):
    cfg = reward_config()
    o = get_db().execute("SELECT sale_date FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not o or o["sale_date"] < cfg["start_date"]:
        return 0
    lines = get_db().execute(
        """SELECT v.size, SUM(i.quantity) FROM order_items i JOIN product_variants v ON v.id = i.variant_id
           WHERE i.order_id = ? GROUP BY v.size""", (order_id,)).fetchall()
    return rewards.order_points([(r[0], r[1]) for r in lines], cfg)


def whatsapp_phone(phone, cfg):
    digits = re.sub(r"\D", "", phone or "").lstrip("0")
    if not digits:
        return ""
    cc = re.sub(r"\D", "", str(cfg.get("country_code") or ""))
    return cc + digits if cc and len(digits) == 10 else digits


def _pack_options(balance, cfg):
    return [{"size": s, "cost": c, "can_take": balance // c}
            for s, c in sorted(cfg["pack_cost"].items(), key=lambda kv: kv[1]) if c and c > 0 and s in SIZES]


def whatsapp_message(shop, st, cfg, gifts, kind="statement", extra=None):
    """Bilingual WhatsApp text (Hindi first). kind: statement | order | redeem | delivered."""
    extra = extra or {}
    bal = st["balance"]
    packs = [p for p in _pack_options(bal, cfg)]
    lines = ["🌶️ *KCM मसाला परिवार*", f"नमस्ते {shop['name']} जी 🙏"]
    en = []
    if kind == "delivered":
        lines = ["🌶️ *KCM मसाला परिवार*",
                 f"🎉 बधाई! {shop['name']} ने मसाला परिवार में *{extra.get('gift_hi', '')}* जीता। साथ रहने के लिए धन्यवाद! 🙏",
                 f"Congratulations! {shop['name']} won a {extra.get('gift_en', 'gift')} in Masala Parivaar. Thank you for staying with us!",
                 f"💳 बाकी पॉइंट / Points left: *{bal}*"]
        return "\n".join(lines)
    if kind == "order" and extra.get("points"):
        lines.append(f"आज की खरीद पर *+{extra['points']} पॉइंट* जुड़े।")
        en.append(f"Points added today: +{extra['points']}")
    if kind == "redeem" and extra.get("item_hi"):
        lines.append(f"आज आपने *{extra['item_hi']}* लिया ({-extra['points']} पॉइंट)।")
        en.append(f"Redeemed today: {extra['item_en']} ({-extra['points']} points)")
    lines.append(f"💳 कुल पॉइंट: *{bal}*")
    en.append(f"Balance: {bal} points")
    takeable = [p for p in packs if p["can_take"] > 0]
    if takeable:
        lines.append("🎁 अभी ले सकते हैं: " + " या ".join(f"{p['can_take']} × {p['size']} फ्री" for p in takeable))
        en.append("Free now: " + " or ".join(f"{p['can_take']} × {p['size']}" for p in takeable))
    elif packs:
        need = packs[0]["cost"] - bal
        lines.append(f"🎁 अगला फ्री {packs[0]['size']}: बस {need} पॉइंट और")
        en.append(f"Next free {packs[0]['size']}: {need} more points")
    if st["next_bonus_in"]:
        lines.append(f"💰 बचत बोनस: हर {cfg['bonus_every']} पॉइंट बचाने पर +{cfg['bonus_points']} — अगला बोनस {st['next_bonus_in']} पॉइंट दूर")
    reach = [gf for gf in gifts if gf["points"] <= bal]
    nxt = next((gf for gf in gifts if gf["points"] > bal), None)
    if reach:
        lines.append(f"🏆 आप गिफ्ट ले सकते हैं: {reach[-1]['name_hi']} ({reach[-1]['points']} पॉइंट)")
    if nxt:
        lines.append(f"🏆 अगला गिफ्ट: {nxt['name_hi']} ({nxt['points']} पॉइंट) — {nxt['points'] - bal} पॉइंट और")
        en.append(f"Next gift: {nxt['name_en']} ({nxt['points'] - bal} more points)")
    if st["expiring_30_days"]:
        lines.append(f"⏳ {st['expiring_30_days']} पॉइंट {st['next_expiry'].strftime('%d-%m-%Y')} तक इस्तेमाल करें")
    lines.append("अभी लेना है या बचाना है? बताइए 🙂")
    lines.append("घर जैसा स्वाद — धन्यवाद! 🙏")
    lines.append("")
    lines.append(" · ".join(en))
    return "\n".join(lines)


def _active_gifts(include_cost=False):
    rows = get_db().execute("SELECT * FROM gifts WHERE active = 1 ORDER BY points, id").fetchall()
    out = []
    for r in rows:
        gft = {"id": r["id"], "name_hi": r["name_hi"], "name_en": r["name_en"], "points": r["points"]}
        if include_cost:
            gft["cost"] = rupees(r["cost_paise"])
        out.append(gft)
    return out


def _shop_or_404(shop_id):
    shop = get_db().execute("SELECT id, name, area, phone, active FROM shops WHERE id = ?", (shop_id,)).fetchone()
    if not shop:
        raise ApiError("Shop not found", 404)
    return shop


def shop_statement(shop_id, msg="statement", extra=None):
    cfg = reward_config()
    shop = _shop_or_404(shop_id)
    st = shop_points(shop_id, cfg)
    gifts = _active_gifts()
    bal = st["balance"]
    text = whatsapp_message(shop, st, cfg, gifts, msg, extra)
    phone = whatsapp_phone(shop["phone"], cfg)
    pending = get_db().execute(
        """SELECT r.id, r.status, r.points, r.redeemed_on, g.name_hi, g.name_en FROM redemptions r JOIN gifts g ON g.id = r.gift_id
           WHERE r.shop_id = ? AND r.kind = 'gift' AND r.status IN ('requested', 'approved') ORDER BY r.id""", (shop_id,)
    ).fetchall()
    return {
        "shop": {"id": shop["id"], "name": shop["name"], "area": shop["area"], "phone": shop["phone"]},
        "balance": bal, "totals": st["totals"], "saved_since_redeem": st["saved_since_redeem"],
        "next_bonus_in": st["next_bonus_in"], "expiring_30_days": st["expiring_30_days"],
        "next_expiry": st["next_expiry"].isoformat() if st["next_expiry"] else None,
        "history": list(reversed(st["history"]))[:150],
        "config": {k: cfg[k] for k in ("earn", "pack_cost", "bonus_every", "bonus_points", "expiry_months", "start_date")},
        "packs": _pack_options(bal, cfg),
        "gifts": [{**gf, "affordable": gf["points"] <= bal} for gf in gifts],
        "pending_gifts": [dict(r) for r in pending],
        "whatsapp": {"text": text, "phone": phone,
                     "url": (f"https://wa.me/{phone}?text=" if phone else "https://wa.me/?text=") + quote(text)},
    }


@app.get("/api/rewards/shops/<int:shop_id>")
@login_required()
def get_shop_rewards(shop_id):
    msg, extra = request.args.get("msg", "statement"), {}
    if msg == "order":
        oid = opt_int(request.args.get("order_id"), "order")
        extra["points"] = order_earned_points(oid) if oid else 0
    return jsonify(shop_statement(shop_id, msg, extra))


@app.patch("/api/shops/<int:shop_id>/phone")
@login_required()
def set_shop_phone(shop_id):
    shop = _shop_or_404(shop_id)
    phone = clean_text(body().get("phone"), "Phone", max_len=20)
    if len(re.sub(r"\D", "", phone)) < 10:
        raise ApiError("Enter a 10-digit mobile number")
    if g.user["role"] != "admin" and shop["phone"]:
        raise ApiError("This shop already has a phone number. Ask the admin to change it.")
    db = get_db()
    with db:
        db.execute("UPDATE shops SET phone = ? WHERE id = ?", (phone, shop_id))
    return jsonify(ok=True)


@app.post("/api/rewards/shops/<int:shop_id>/redeem")
@login_required()
def redeem(shop_id):
    shop = _shop_or_404(shop_id)
    d = body()
    cfg = reward_config()
    db = get_db()
    kind = d.get("kind")
    if kind == "pack":
        size = d.get("size")
        cost = int(cfg["pack_cost"].get(size) or 0) if size in SIZES else 0
        if cost <= 0:
            raise ApiError("Choose which free pack to give")
        qty = opt_int(d.get("quantity"), "quantity") or 1
        if not 1 <= qty <= 100:
            raise ApiError("Invalid quantity")
        pid = opt_int(d.get("product_id"), "product")
        prod = pid and db.execute("SELECT name FROM products WHERE id = ? AND active = 1", (pid,)).fetchone()
        if not prod:
            raise ApiError("Choose the flavour (product) of the free pack")
        row = (shop_id, "pack", size, pid, qty, None, cost * qty, "delivered")
        extra = {"points": cost * qty, "item_hi": f"{qty} {size} {prod[0]} फ्री", "item_en": f"free {qty} × {size} {prod[0]}"}
    elif kind == "gift":
        gid = opt_int(d.get("gift_id"), "gift")
        gift = gid and db.execute("SELECT * FROM gifts WHERE id = ? AND active = 1", (gid,)).fetchone()
        if not gift:
            raise ApiError("Choose a gift")
        row = (shop_id, "gift", None, None, 1, gid, gift["points"], "requested")
        extra = {"points": gift["points"], "item_hi": f"🎁 {gift['name_hi']} (जल्द पहुँचेगा)", "item_en": f"{gift['name_en']} (on its way)"}
    else:
        raise ApiError("Choose a free pack or a gift")
    have = shop_points(shop_id, cfg)["balance"]
    if have < row[6]:
        raise ApiError(f"Not enough points. {shop['name']} has {have} points; this needs {row[6]}.")
    with db:
        rid = db.execute(
            """INSERT INTO redemptions (shop_id, kind, size, product_id, quantity, gift_id, points, status, redeemed_on, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", row + (today().isoformat(), g.user["id"])).lastrowid
        st = shop_points(shop_id, cfg)
        if not st["ok"]:
            raise ApiError(f"Not enough points. {shop['name']} has {have} points; this needs {row[6]}.")
    return jsonify(ok=True, id=rid, statement=shop_statement(shop_id, "redeem", extra)), 201


# ----- admin: rewards overview, settings, gifts, redemptions, adjustments
@app.get("/api/admin/rewards")
@login_required("admin")
def admin_rewards():
    t = parse_date(request.args.get("to") or today().isoformat(), "to date")
    f = parse_date(request.args.get("from") or (t - timedelta(days=29)).isoformat(), "from date")
    db = get_db()
    cfg = reward_config()
    shops = []
    for s in db.execute(
        """SELECT s.id, s.name, s.area, s.phone, (SELECT MAX(sale_date) FROM orders o WHERE o.shop_id = s.id) AS last_order
           FROM shops s WHERE s.active = 1 ORDER BY s.name COLLATE NOCASE"""
    ):
        st = shop_points(s["id"], cfg)
        shops.append({"id": s["id"], "name": s["name"], "area": s["area"], "phone": s["phone"], "last_order": s["last_order"],
                      "balance": st["balance"], "earned": st["totals"]["earned"] + st["totals"]["bonus"],
                      "redeemed": st["totals"]["redeemed"], "saved": st["saved_since_redeem"],
                      "next_bonus_in": st["next_bonus_in"], "expiring": st["expiring_30_days"]})
    shops.sort(key=lambda x: -x["balance"])

    period = (f.isoformat(), t.isoformat())
    reds = db.execute(
        """SELECT r.*, s.name AS shop_name, s.area, s.phone, g.name_en, g.name_hi, g.cost_paise, p.name AS product,
                  u.full_name AS by_name
           FROM redemptions r JOIN shops s ON s.id = r.shop_id LEFT JOIN gifts g ON g.id = r.gift_id
           LEFT JOIN products p ON p.id = r.product_id LEFT JOIN users u ON u.id = r.created_by
           WHERE (r.redeemed_on BETWEEN ? AND ?) OR (r.kind = 'gift' AND r.status IN ('requested', 'approved'))
           ORDER BY r.id DESC LIMIT 300""", period).fetchall()

    def avg_price(size):
        for where, params in (("o.sale_date BETWEEN ? AND ?", period), ("1 = 1", ())):
            r = db.execute(f"""SELECT SUM(i.quantity * i.unit_price_paise) * 1.0 / SUM(i.quantity) FROM orders o
                               JOIN order_items i ON i.order_id = o.id JOIN product_variants v ON v.id = i.variant_id
                               WHERE v.size = ? AND {where}""", (size, *params)).fetchone()[0]
            if r:
                return r
        return 0
    prices = {s: avg_price(s) for s in SIZES}
    pack_cost = gift_cost = 0
    for r in reds:
        if r["status"] == "cancelled" or not (period[0] <= r["redeemed_on"] <= period[1]):
            continue
        if r["kind"] == "pack":
            pack_cost += r["quantity"] * prices.get(r["size"], 0)
        else:
            gift_cost += r["cost_paise"] or 0
    sales = db.execute("""SELECT COALESCE(SUM(i.quantity * i.unit_price_paise), 0) FROM orders o
                          JOIN order_items i ON i.order_id = o.id WHERE o.sale_date BETWEEN ? AND ?""", period).fetchone()[0]
    adj = db.execute("""SELECT a.*, s.name AS shop_name, u.full_name AS by_name FROM point_adjustments a
                        JOIN shops s ON s.id = a.shop_id JOIN users u ON u.id = a.created_by ORDER BY a.id DESC LIMIT 100""").fetchall()
    return jsonify(
        from_date=f.isoformat(), to_date=t.isoformat(), config=cfg,
        gifts=[{**dict(r), "cost": rupees(r["cost_paise"])} for r in db.execute("SELECT * FROM gifts ORDER BY active DESC, points, id")],
        shops=shops,
        summary={"outstanding": sum(s["balance"] for s in shops), "shops_with_points": sum(1 for s in shops if s["balance"] > 0),
                 "pack_cost": rupees(pack_cost), "gift_cost": rupees(gift_cost), "sales": rupees(sales),
                 "cost_pct": round((pack_cost + gift_cost) * 100 / sales, 1) if sales else 0,
                 "redeemed_points": sum(r["points"] for r in reds if r["status"] != "cancelled" and period[0] <= r["redeemed_on"] <= period[1])},
        redemptions=[{"id": r["id"], "shop_id": r["shop_id"], "shop_name": r["shop_name"], "area": r["area"], "kind": r["kind"],
                      "size": r["size"], "product": r["product"], "quantity": r["quantity"], "gift_hi": r["name_hi"],
                      "gift_en": r["name_en"], "points": r["points"], "status": r["status"], "date": r["redeemed_on"],
                      "by": r["by_name"]} for r in reds],
        adjustments=[{"id": a["id"], "shop_name": a["shop_name"], "points": a["points"], "reason": a["reason"],
                      "date": a["adj_date"], "by": a["by_name"]} for a in adj],
    )


@app.put("/api/admin/rewards/config")
@login_required("admin")
def update_reward_config():
    d = body()
    cfg = reward_config()

    def num(v, field, lo=0, hi=100000):
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise ApiError(f"{field} must be a whole number")
        if not lo <= n <= hi:
            raise ApiError(f"{field} must be between {lo} and {hi}")
        return n
    new = dict(cfg)
    new["earn"] = {s: num((d.get("earn") or {}).get(s, cfg["earn"].get(s, 0)), f"Points for {s}") for s in SIZES}
    new["pack_cost"] = {s: num((d.get("pack_cost") or {}).get(s, cfg["pack_cost"].get(s, 0)), f"Free {s} cost") for s in SIZES}
    new["bonus_every"] = num(d.get("bonus_every", cfg["bonus_every"]), "Saving bonus 'every'")
    new["bonus_points"] = num(d.get("bonus_points", cfg["bonus_points"]), "Saving bonus points")
    new["expiry_months"] = num(d.get("expiry_months", cfg["expiry_months"]), "Expiry months", 0, 120)
    new["start_date"] = parse_date(d.get("start_date", cfg["start_date"]), "start date").isoformat()
    new["country_code"] = re.sub(r"\D", "", str(d.get("country_code", cfg.get("country_code", "91"))))[:4]
    db = get_db()
    shop_ids = [r[0] for r in db.execute("SELECT DISTINCT shop_id FROM redemptions UNION SELECT DISTINCT shop_id FROM point_adjustments")]
    for sid in shop_ids:  # changing the rules recomputes history; never let a shop go negative
        if not shop_points(sid, new)["ok"]:
            name = db.execute("SELECT name FROM shops WHERE id = ?", (sid,)).fetchone()[0]
            raise ApiError(f"These settings would make {name}'s points negative (they already redeemed). Not saved.")
    with db:
        db.execute("INSERT OR REPLACE INTO reward_settings (key, value) VALUES ('config', ?)", (json.dumps(new),))
    return jsonify(ok=True, config=new)


def _gift_fields(d, current=None):
    current = current or {}
    name_hi = clean_text(d.get("name_hi", current.get("name_hi")), "Hindi name")
    name_en = clean_text(d.get("name_en", current.get("name_en")), "English name")
    try:
        points = int(d.get("points", current.get("points")))
        cost = round(float(d.get("cost", rupees(current.get("cost_paise", 0))) or 0) * 100)
    except (TypeError, ValueError):
        raise ApiError("Points and cost must be numbers")
    if not 1 <= points <= 1_000_000 or cost < 0:
        raise ApiError("Invalid points or cost")
    active = 1 if d.get("active", current.get("active", 1)) else 0
    return name_hi, name_en, points, cost, active


@app.post("/api/admin/gifts")
@login_required("admin")
def add_gift():
    vals = _gift_fields(body())
    db = get_db()
    with db:
        db.execute("INSERT INTO gifts (name_hi, name_en, points, cost_paise, active) VALUES (?, ?, ?, ?, ?)", vals)
    return jsonify(ok=True), 201


@app.patch("/api/admin/gifts/<int:gid>")
@login_required("admin")
def update_gift(gid):
    db = get_db()
    cur = db.execute("SELECT * FROM gifts WHERE id = ?", (gid,)).fetchone()
    if not cur:
        raise ApiError("Gift not found", 404)
    vals = _gift_fields(body(), dict(cur))
    with db:
        db.execute("UPDATE gifts SET name_hi = ?, name_en = ?, points = ?, cost_paise = ?, active = ? WHERE id = ?", vals + (gid,))
    return jsonify(ok=True)


@app.patch("/api/admin/redemptions/<int:rid>")
@login_required("admin")
def update_redemption(rid):
    status = body().get("status")
    db = get_db()
    r = db.execute("""SELECT r.*, g.name_hi, g.name_en FROM redemptions r LEFT JOIN gifts g ON g.id = r.gift_id
                      WHERE r.id = ?""", (rid,)).fetchone()
    if not r:
        raise ApiError("Redemption not found", 404)
    allowed = {"requested": {"approved", "delivered", "cancelled"}, "approved": {"delivered", "cancelled"},
               "delivered": {"cancelled"}, "cancelled": set()}
    if status not in allowed[r["status"]]:
        raise ApiError(f"Cannot change from {r['status']} to {status}")
    with db:  # cancelling gives the points back (the redemption stops counting)
        db.execute("UPDATE redemptions SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (status, rid))
    out = {"ok": True}
    if status == "delivered" and r["kind"] == "gift":
        out["statement"] = shop_statement(r["shop_id"], "delivered", {"gift_hi": r["name_hi"], "gift_en": r["name_en"]})
    return jsonify(out)


@app.post("/api/admin/rewards/adjust")
@login_required("admin")
def adjust_points():
    d = body()
    shop = _shop_or_404(opt_int(d.get("shop_id"), "shop") or 0)
    try:
        pts = int(d.get("points"))
    except (TypeError, ValueError):
        raise ApiError("Points must be a whole number, e.g. 5 or -5")
    if pts == 0 or abs(pts) > 100000:
        raise ApiError("Enter the points to add (e.g. 5) or remove (e.g. -5)")
    reason = clean_text(d.get("reason"), "Reason", max_len=200)
    db = get_db()
    with db:
        db.execute("INSERT INTO point_adjustments (shop_id, points, reason, adj_date, created_by) VALUES (?, ?, ?, ?, ?)",
                   (shop["id"], pts, reason, today().isoformat(), g.user["id"]))
        if not shop_points(shop["id"])["ok"]:
            raise ApiError("The shop doesn't have that many points to remove")
    return jsonify(ok=True, statement=shop_statement(shop["id"]))


# ============================================================================ shop history (all salespeople)
@app.get("/api/shops/<int:shop_id>/history")
@login_required()
def shop_history(shop_id):
    db = get_db()
    shop = db.execute(
        """SELECT s.*, u.full_name AS added_by FROM shops s LEFT JOIN users u ON u.id = s.created_by WHERE s.id = ?""",
        (shop_id,)).fetchone()
    if not shop:
        raise ApiError("Shop not found", 404)
    summ = db.execute(
        f"""SELECT COUNT(DISTINCT o.id) AS orders, MIN(o.sale_date) AS first, MAX(o.sale_date) AS last, {SUMS}
            {ITEMS_JOIN} WHERE o.shop_id = ?""", (shop_id,)).fetchone()
    top = db.execute(
        """SELECT p.name, v.size, SUM(i.quantity) AS qty, COUNT(DISTINCT o.id) AS times, MAX(o.sale_date) AS last_date,
                  (SELECT i2.unit_price_paise FROM order_items i2 JOIN orders o2 ON o2.id = i2.order_id
                   WHERE o2.shop_id = o.shop_id AND i2.variant_id = i.variant_id
                   ORDER BY o2.sale_date DESC, o2.id DESC LIMIT 1) AS last_price
           FROM orders o JOIN order_items i ON i.order_id = o.id JOIN product_variants v ON v.id = i.variant_id
           JOIN products p ON p.id = v.product_id
           WHERE o.shop_id = ? GROUP BY i.variant_id ORDER BY qty DESC LIMIT 20""", (shop_id,)).fetchall()
    reds = db.execute(
        """SELECT r.*, g.name_hi, g.name_en, p.name AS product, u.full_name AS by_name FROM redemptions r
           LEFT JOIN gifts g ON g.id = r.gift_id LEFT JOIN products p ON p.id = r.product_id
           LEFT JOIN users u ON u.id = r.created_by WHERE r.shop_id = ? ORDER BY r.id DESC LIMIT 200""", (shop_id,)).fetchall()
    orders = load_orders("o.shop_id = ?", [shop_id], limit=300)
    last = summ["last"]
    cfg = reward_config()
    return jsonify(
        shop={"id": shop["id"], "name": shop["name"], "area": shop["area"], "phone": shop["phone"], "active": bool(shop["active"]),
              "added_by": shop["added_by"], "added_on": (shop["created_at"] or "")[:10],
              "wa_phone": whatsapp_phone(shop["phone"], cfg)},
        summary={"orders": summ["orders"], "first": summ["first"], "last": last,
                 "days_since": (today() - date.fromisoformat(last)).days if last else None,
                 "revenue": rupees(summ["revenue"]), "units": units_of(summ),
                 "avg": rupees(summ["revenue"] / summ["orders"]) if summ["orders"] else 0,
                 "points": shop_points(shop_id, cfg)["balance"]},
        top=[{"product": r["name"], "size": r["size"], "qty": r["qty"], "times": r["times"],
              "last_date": r["last_date"], "last_price": rupees(r["last_price"])} for r in top],
        orders=orders,
        redemptions=[{"id": r["id"], "date": r["redeemed_on"], "kind": r["kind"], "size": r["size"], "quantity": r["quantity"],
                      "product": r["product"], "gift_hi": r["name_hi"], "gift_en": r["name_en"], "points": r["points"],
                      "status": r["status"], "by": r["by_name"]} for r in reds],
        truncated=len(orders) >= 300,
    )


# ============================================================================ salesperson stock
LOW_STOCK_PCT = 30   # an item is "low" when less than this % of its limit is left


def _utc(dt):
    """Local naive datetime -> 'YYYY-MM-DD HH:MM:SS' in UTC (the format of CURRENT_TIMESTAMP)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _local_hm(utc_text):
    try:
        return datetime.strptime(utc_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone().strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


def stock_for(user_id):
    """{variant_id: {...}} for every product size this salesperson has a limit row for."""
    db = get_db()
    rows = db.execute(
        """SELECT l.variant_id, l.limit_qty, l.started_at, v.size, p.id AS product_id, p.name, p.active
           FROM stock_limits l JOIN product_variants v ON v.id = l.variant_id JOIN products p ON p.id = v.product_id
           WHERE l.user_id = ?""", (user_id,)).fetchall()
    out = {r["variant_id"]: {"variant_id": r["variant_id"], "product_id": r["product_id"], "product": r["name"],
                             "size": r["size"], "limit": r["limit_qty"], "active": bool(r["active"]),
                             "moves": 0, "sold": 0, "free": 0, "sold_today": 0} for r in rows}
    if not out:
        return out
    for vid, q in db.execute("SELECT variant_id, SUM(qty) FROM stock_moves WHERE user_id = ? GROUP BY variant_id", (user_id,)):
        if vid in out:
            out[vid]["moves"] = q
    for vid, q, qt in db.execute(
        """SELECT i.variant_id, SUM(i.quantity), SUM(CASE WHEN o.sale_date = ? THEN i.quantity ELSE 0 END)
           FROM order_items i JOIN orders o ON o.id = i.order_id
           JOIN stock_limits l ON l.user_id = o.user_id AND l.variant_id = i.variant_id
           WHERE o.user_id = ? AND o.created_at >= l.started_at GROUP BY i.variant_id""", (today().isoformat(), user_id)):
        out[vid]["sold"], out[vid]["sold_today"] = q, qt
    for vid, q in db.execute(
        """SELECT v.id, SUM(r.quantity) FROM redemptions r
           JOIN product_variants v ON v.product_id = r.product_id AND v.size = r.size
           JOIN stock_limits l ON l.user_id = r.created_by AND l.variant_id = v.id
           WHERE r.created_by = ? AND r.kind = 'pack' AND r.status != 'cancelled' AND r.created_at >= l.started_at
           GROUP BY v.id""", (user_id,)):
        out[vid]["free"] = q
    for it in out.values():
        it["have"] = it["moves"] - it["sold"] - it["free"]
        lim, have = it["limit"], it["have"]
        it["level"] = "zero" if have <= 0 else "low" if lim and have * 100 < lim * LOW_STOCK_PCT else "ok"
        it["refill_qty"] = max(0, lim - have)
    return out


def _stock_list(user_id):
    items = [it for it in stock_for(user_id).values() if it["limit"] > 0 and it["active"]]
    order = {s: n for n, s in enumerate(SIZES)}
    items.sort(key=lambda it: (it["product"].lower(), order.get(it["size"], 99)))
    return items


def _stock_log(user_id):
    """Today's stock events (local day): refills, +/-, sales and free packs."""
    db = get_db()
    since = _utc(datetime.combine(today(), dtime.min))
    names = {r[0]: f"{r[1]} {r[2]}" for r in db.execute(
        "SELECT v.id, p.name, v.size FROM product_variants v JOIN products p ON p.id = v.product_id")}
    tracked = {r[0] for r in db.execute("SELECT variant_id FROM stock_limits WHERE user_id = ?", (user_id,))}
    log = []
    icons = {"start": "🏁", "refill": "🔄", "add": "＋", "remove": "−"}
    words = {"start": "Start", "refill": "Refill", "add": "Added", "remove": "Removed"}
    for m in db.execute("SELECT * FROM stock_moves WHERE user_id = ? AND created_at >= ?", (user_id, since)):
        log.append((m["created_at"], f"{icons[m['kind']]} {words[m['kind']]} {names.get(m['variant_id'], '')}: "
                                     f"{'+' if m['qty'] > 0 else ''}{m['qty']}"))
    orders = {}
    for r in db.execute(
        """SELECT o.id, o.created_at, s.name AS shop, i.variant_id, i.quantity FROM orders o
           JOIN order_items i ON i.order_id = o.id JOIN shops s ON s.id = o.shop_id
           WHERE o.user_id = ? AND o.created_at >= ?""", (user_id, since)):
        if r["variant_id"] in tracked:
            o = orders.setdefault(r["id"], {"at": r["created_at"], "shop": r["shop"], "lines": []})
            o["lines"].append(f"{names.get(r['variant_id'], '')} −{r['quantity']}")
    for o in orders.values():
        log.append((o["at"], f"🛒 {o['shop']}: " + ", ".join(o["lines"])))
    for r in db.execute(
        """SELECT r.created_at, r.quantity, r.size, p.name, s.name AS shop FROM redemptions r
           JOIN products p ON p.id = r.product_id JOIN shops s ON s.id = r.shop_id
           WHERE r.created_by = ? AND r.kind = 'pack' AND r.status != 'cancelled' AND r.created_at >= ?""", (user_id, since)):
        log.append((r["created_at"], f"🎁 Free pack to {r['shop']}: {r['name']} {r['size']} −{r['quantity']}"))
    log.sort(key=lambda x: x[0], reverse=True)
    return [{"time": _local_hm(at), "text": text} for at, text in log]


@app.get("/api/my/stock")
@login_required("sales")
def my_stock():
    uid = g.user["id"]
    limits = {r["variant_id"]: r["limit_qty"] for r in get_db().execute(
        "SELECT variant_id, limit_qty FROM stock_limits WHERE user_id = ?", (uid,))}
    return jsonify(items=_stock_list(uid), log=_stock_log(uid), limits=limits, products=active_products(),
                   low_pct=LOW_STOCK_PCT)


@app.put("/api/my/stock/limits")
@login_required("sales")
def set_stock_limits():
    d = body().get("limits")
    if not isinstance(d, list):
        raise ApiError("No limits were sent")
    db = get_db()
    valid = {r[0] for r in db.execute(
        "SELECT v.id FROM product_variants v JOIN products p ON p.id = v.product_id WHERE p.active = 1")}
    uid = g.user["id"]
    current = {r["variant_id"]: r["limit_qty"] for r in db.execute(
        "SELECT variant_id, limit_qty FROM stock_limits WHERE user_id = ?", (uid,))}
    with db:
        for it in d:
            try:
                vid, lim = int(it["variant_id"]), int(it.get("limit") or 0)
            except (KeyError, TypeError, ValueError):
                raise ApiError("Limits must be whole numbers")
            if vid not in valid or not 0 <= lim <= 10000:
                raise ApiError("Invalid limit")
            if vid not in current:
                if lim == 0:
                    continue
                # first time: start counting from now, assuming they are carrying a full load
                db.execute("INSERT INTO stock_limits (user_id, variant_id, limit_qty) VALUES (?, ?, ?)", (uid, vid, lim))
                db.execute("INSERT INTO stock_moves (user_id, variant_id, qty, kind) VALUES (?, ?, ?, 'start')", (uid, vid, lim))
            elif current[vid] != lim:
                db.execute("UPDATE stock_limits SET limit_qty = ? WHERE user_id = ? AND variant_id = ?", (lim, uid, vid))
    return jsonify(ok=True, items=_stock_list(uid), log=_stock_log(uid))


def _move(uid, vid, qty, kind):
    if qty:
        get_db().execute("INSERT INTO stock_moves (user_id, variant_id, qty, kind) VALUES (?, ?, ?, ?)", (uid, vid, qty, kind))


@app.post("/api/my/stock/<int:vid>/refill")
@login_required("sales")
def refill_stock(vid):
    uid = g.user["id"]
    it = stock_for(uid).get(vid)
    if not it or not it["limit"]:
        raise ApiError("Set a limit for this product first")
    db = get_db()
    with db:
        _move(uid, vid, it["refill_qty"], "refill")
    return jsonify(ok=True, added=it["refill_qty"], items=_stock_list(uid), log=_stock_log(uid))


@app.post("/api/my/stock/refill-all")
@login_required("sales")
def refill_all_stock():
    uid = g.user["id"]
    db = get_db()
    n = 0
    with db:
        for it in _stock_list(uid):
            if it["refill_qty"] > 0:
                _move(uid, it["variant_id"], it["refill_qty"], "refill")
                n += 1
    return jsonify(ok=True, refilled=n, items=_stock_list(uid), log=_stock_log(uid))


@app.post("/api/my/stock/<int:vid>/step")
@login_required("sales")
def step_stock(vid):
    uid = g.user["id"]
    step = body().get("qty")
    if step not in (1, -1):
        raise ApiError("Use +1 or -1")
    it = stock_for(uid).get(vid)
    if not it or not it["limit"]:
        raise ApiError("Set a limit for this product first")
    if step < 0 and it["have"] <= 0:
        raise ApiError("Stock is already 0")
    db = get_db()
    with db:
        _move(uid, vid, step, "add" if step > 0 else "remove")
    return jsonify(ok=True, items=_stock_list(uid), log=_stock_log(uid))


@app.get("/api/admin/stock")
@login_required("admin")
def admin_stock():
    db = get_db()
    people = db.execute("SELECT id, full_name FROM users WHERE role = 'sales' AND active = 1 ORDER BY full_name COLLATE NOCASE").fetchall()
    rows = {}
    for u in people:
        for it in _stock_list(u["id"]):
            key = (it["product"], it["size"])
            r = rows.setdefault(key, {"product": it["product"], "size": it["size"], "by_user": {}})
            r["by_user"][u["id"]] = {"have": it["have"], "limit": it["limit"], "level": it["level"]}
    order = {s: n for n, s in enumerate(SIZES)}
    since = (today() - timedelta(days=30)).isoformat()
    moves = db.execute(
        """SELECT m.created_at, m.qty, m.kind, u.full_name, p.name, v.size FROM stock_moves m
           JOIN users u ON u.id = m.user_id JOIN product_variants v ON v.id = m.variant_id JOIN products p ON p.id = v.product_id
           WHERE m.kind IN ('refill', 'add') AND m.created_at >= ? ORDER BY m.id DESC LIMIT 300""", (since,)).fetchall()
    return jsonify(
        salespeople=[{"id": u["id"], "name": u["full_name"]} for u in people],
        rows=sorted(rows.values(), key=lambda r: (r["product"].lower(), order.get(r["size"], 99))),
        moves=[{"date": m["created_at"][:10], "time": _local_hm(m["created_at"]), "who": m["full_name"],
                "item": f"{m['name']} {m['size']}", "qty": m["qty"], "kind": m["kind"]} for m in moves],
    )


# ============================================================================ admin: products
@app.get("/api/admin/products")
@login_required("admin")
def admin_products():
    stats = {
        (r["product_id"], r["size"]): r
        for r in get_db().execute(
            """SELECT v.product_id, v.size, SUM(i.quantity) AS qty, SUM(i.quantity * i.unit_price_paise) AS rev
               FROM order_items i JOIN product_variants v ON v.id = i.variant_id GROUP BY v.product_id, v.size"""
        )
    }
    out = []
    for p in active_products():
        avg = {}
        for size in SIZES:
            r = stats.get((p["id"], size))
            avg[size] = rupees(r["rev"] / r["qty"]) if r and r["qty"] else None
        out.append({"id": p["id"], "name": p["name"], "avg_price": avg})
    return jsonify(products=out)


def _product_taken(db, name, exclude_id=0):
    return db.execute("SELECT 1 FROM products WHERE active = 1 AND name = ? COLLATE NOCASE AND id != ?",
                      (name, exclude_id)).fetchone()


@app.post("/api/admin/products")
@login_required("admin")
def add_product():
    name = clean_text(body().get("name"), "Product name")
    db = get_db()
    if _product_taken(db, name):
        raise ApiError("A product with that name already exists")
    with db:
        pid = db.execute("INSERT INTO products (name) VALUES (?)", (name,)).lastrowid
        db.executemany("INSERT INTO product_variants (product_id, size) VALUES (?, ?)", [(pid, s) for s in SIZES])
    return jsonify(ok=True, id=pid), 201


@app.patch("/api/admin/products/<int:pid>")
@login_required("admin")
def rename_product(pid):
    name = clean_text(body().get("name"), "Product name")
    db = get_db()
    if not db.execute("SELECT 1 FROM products WHERE id = ? AND active = 1", (pid,)).fetchone():
        raise ApiError("Product not found", 404)
    if _product_taken(db, name, pid):
        raise ApiError("A product with that name already exists")
    with db:
        db.execute("UPDATE products SET name = ? WHERE id = ?", (name, pid))
    return jsonify(ok=True)


@app.delete("/api/admin/products/<int:pid>")
@login_required("admin")
def remove_product(pid):
    db = get_db()
    with db:
        cur = db.execute("UPDATE products SET active = 0 WHERE id = ? AND active = 1", (pid,))
    if cur.rowcount == 0:
        raise ApiError("Product not found", 404)
    return jsonify(ok=True)


# ============================================================================ admin: shops
@app.get("/api/admin/shops")
@login_required("admin")
def admin_shops():
    rows = get_db().execute(
        """SELECT s.id, s.name, s.area, s.phone, u.full_name AS added_by,
                  (SELECT COUNT(*) FROM orders o WHERE o.shop_id = s.id) AS visits,
                  (SELECT MAX(sale_date) FROM orders o WHERE o.shop_id = s.id) AS last_sale,
                  (SELECT COALESCE(SUM(i.quantity * i.unit_price_paise), 0) FROM orders o
                     JOIN order_items i ON i.order_id = o.id WHERE o.shop_id = s.id) AS revenue
           FROM shops s LEFT JOIN users u ON u.id = s.created_by
           WHERE s.active = 1 ORDER BY s.name COLLATE NOCASE, s.area COLLATE NOCASE"""
    ).fetchall()
    return jsonify(shops=[{**shop_json(r), "added_by": r["added_by"], "visits": r["visits"],
                           "last_sale": r["last_sale"], "revenue": rupees(r["revenue"])} for r in rows])


@app.patch("/api/admin/shops/<int:sid>")
@login_required("admin")
def update_shop(sid):
    name, area, phone = _shop_fields(body())
    db = get_db()
    if not db.execute("SELECT 1 FROM shops WHERE id = ? AND active = 1", (sid,)).fetchone():
        raise ApiError("Shop not found", 404)
    if _shop_taken(db, name, area, sid):
        raise ApiError("Another shop with the same name and area already exists")
    with db:
        db.execute("UPDATE shops SET name = ?, area = ?, phone = ? WHERE id = ?", (name, area, phone, sid))
    return jsonify(ok=True)


@app.delete("/api/admin/shops/<int:sid>")
@login_required("admin")
def remove_shop(sid):
    db = get_db()
    with db:
        cur = db.execute("UPDATE shops SET active = 0 WHERE id = ? AND active = 1", (sid,))
    if cur.rowcount == 0:
        raise ApiError("Shop not found", 404)
    return jsonify(ok=True)


# ============================================================================ admin: users
@app.get("/api/admin/users")
@login_required("admin")
def list_users():
    rows = get_db().execute(
        "SELECT id, username, full_name, role, active FROM users ORDER BY active DESC, role, full_name COLLATE NOCASE"
    ).fetchall()
    return jsonify(users=[dict(r) for r in rows], me=g.user["id"])


@app.post("/api/admin/users")
@login_required("admin")
def add_user():
    d = body()
    username = str(d.get("username") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,30}", username):
        raise ApiError("Username must be 3–30 characters: letters, numbers, dot, dash or underscore")
    full_name = clean_text(d.get("full_name"), "Full name")
    role = d.get("role", "sales")
    if role not in ("sales", "admin"):
        raise ApiError("Invalid role")
    pw = check_password(d.get("password"))
    db = get_db()
    try:
        with db:
            db.execute("INSERT INTO users (username, password_hash, full_name, role) VALUES (?, ?, ?, ?)",
                       (username, generate_password_hash(pw), full_name, role))
    except sqlite3.IntegrityError:
        raise ApiError("That username is already taken")
    return jsonify(ok=True), 201


@app.patch("/api/admin/users/<int:user_id>")
@login_required("admin")
def update_user(user_id):
    d = body()
    db = get_db()
    if not db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
        raise ApiError("User not found", 404)
    sets, params = [], []
    if "full_name" in d:
        sets.append("full_name = ?"); params.append(clean_text(d["full_name"], "Full name"))
    if "password" in d:
        sets.append("password_hash = ?"); params.append(generate_password_hash(check_password(d["password"])))
    if "active" in d:
        if user_id == g.user["id"] and not d["active"]:
            raise ApiError("You cannot deactivate your own account")
        sets.append("active = ?"); params.append(1 if d["active"] else 0)
    if not sets:
        raise ApiError("Nothing to update")
    with db:
        db.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params + [user_id])
    return jsonify(ok=True)


# ============================================================================ frontend
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.path.startswith("/api/") or request.path == "/" or request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store" if request.path.startswith("/api/") else "no-cache"
    return resp


init_db()

if __name__ == "__main__":
    # Development server. For real use run:  waitress-serve --port=8000 app:app
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)

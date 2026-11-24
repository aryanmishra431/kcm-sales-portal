"""Create a demo database with realistic sample data (no real business data).

    python demo_seed.py            # creates demo.db
    DATABASE=demo.db python app.py # run the app on it

Logins created:  admin / admin123   ·   rahul / demo123   ·   amit / demo123
"""
import os
import random
import sys
import time
from datetime import date, timedelta

DB = os.environ.get("DATABASE", "demo.db")
if os.path.exists(DB):
    sys.exit(f"{DB} already exists; delete it first or set DATABASE=another.db")
os.environ["DATABASE"] = DB
os.environ.setdefault("ADMIN_PASSWORD", "admin123")

import app  # noqa: E402  (creates the schema and the admin user)

random.seed(7)
H = {"X-Requested-With": "XMLHttpRequest"}
c = app.app.test_client()


def call(method, url, **kw):
    r = getattr(c, method)(url, headers=H, **kw)
    if r.status_code >= 400:
        raise SystemExit(f"{method.upper()} {url} -> {r.status_code} {r.get_json()}")
    return r.get_json()


def login(u, p):
    call("post", "/api/logout")
    call("post", "/api/login", json={"username": u, "password": p})


PRODUCTS = ["Garam Masala", "Jeera", "Kaali Mirch", "Elaichi", "Laung", "Lal Mirch", "Ajwain", "Kasuri Methi",
            "Tejpatta", "Soanf", "Rai", "Kalonji"]
PRICES = {"SMALL": (8, 12), "BIG": (35, 50), "20 BIG": (650, 800)}
SHOPS = [("Sharma Kirana Store", "Palwal Road"), ("Gupta General Store", "Sohna"), ("Bansal Traders", "Farukhnagar"),
         ("Jain Provision Store", "Dankot"), ("Shree Ram Kirana", "Doaj"), ("Mittal Store", "Sohna"),
         ("Aggarwal Kirana", "Palwal Road"), ("Om Sai Traders", "Farukhnagar"), ("Krishna General Store", "Doaj"),
         ("New Bharat Store", "Dankot")]

login("admin", os.environ["ADMIN_PASSWORD"])
for p in PRODUCTS:
    call("post", "/api/admin/products", json={"name": p})
for name, user in [("Rahul Verma", "rahul"), ("Amit Sharma", "amit")]:
    call("post", "/api/admin/users", json={"full_name": name, "username": user, "password": "demo123", "role": "sales"})
# points from 45 days ago so the demo has rewards history
cfg = call("get", "/api/admin/rewards")["config"]
cfg["start_date"] = (date.today() - timedelta(days=45)).isoformat()
call("put", "/api/admin/rewards/config", json=cfg)

today = date.today()
for user, my_shops, workdays in [("rahul", SHOPS[:6], 0.8), ("amit", SHOPS[4:], 0.6)]:
    login(user, "demo123")
    ids = []
    for i, (n, a) in enumerate(my_shops):
        try:
            ids.append(call("post", "/api/shops", json={"name": n, "area": a, "phone": f"98{random.randint(10000000, 99999999)}"})["shop"]["id"])
        except SystemExit:  # shop already added by the other salesperson
            ids.append(next(s["id"] for s in call("get", "/api/shops")["shops"] if s["name"] == n))
    products = call("get", "/api/products")["products"]
    limits = [{"variant_id": p["variants"][s], "limit": lim} for p in products[:8] for s, lim in (("BIG", 10), ("SMALL", 20))]
    for back in range(40, -1, -1):
        d = today - timedelta(days=back)
        if back == 0:  # stock tracking starts this morning with a full load
            time.sleep(1.1)   # timestamps have 1-second precision: keep the old sales before the start
            call("put", "/api/my/stock/limits", json={"limits": limits})
            time.sleep(1.1)
        if random.random() > workdays and back:
            continue
        for sid in random.sample(ids, k=random.randint(1, 3)):
            items = []
            for p in random.sample(products, k=random.randint(2, 4)):
                size = random.choices(["SMALL", "BIG", "20 BIG"], weights=[5, 4, 1])[0]
                lo, hi = PRICES[size]
                items.append({"variant_id": p["variants"][size], "qty": random.randint(1, 3 if size == "20 BIG" else 12),
                              "price": random.randint(lo, hi)})
            call("post", "/api/my/orders", json={"date": d.isoformat(), "shop_id": sid, "items": items})
        if random.random() < 0.5:
            call("post", "/api/my/expenses", json={"date": d.isoformat(), "category": random.choice(["Petrol", "Food", "Travel"]),
                                                  "amount": random.choice([60, 80, 120, 150, 200]), "note": ""})
    # a few free packs from saved points
    for sid in ids[:2]:
        st = call("get", f"/api/rewards/shops/{sid}")
        if st["balance"] >= 10:
            call("post", f"/api/rewards/shops/{sid}/redeem", json={"kind": "pack", "size": "BIG", "quantity": 1, "product_id": products[0]["id"]})
    call("post", f"/api/my/stock/{products[0]['variants']['BIG']}/step", json={"qty": 1})

print(f"Demo database created: {DB}")
print("Logins: admin / admin123 · rahul / demo123 · amit / demo123")

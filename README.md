# 🌶️ KCM Spices: Field Sales & Loyalty Portal

A mobile-first web app built for a real spice distributor whose salespeople sell packs to small kirana shops.
It replaced pen-and-paper sales records with a portal that tracks **sales, shop loyalty points, stock, expenses
and performance**, in **Hindi + English**, and works on low-cost Android phones.

> Built with **Python (Flask) + SQLite + vanilla JavaScript**: no frameworks, no build step, deployed on PythonAnywhere.

| Salesperson dashboard | New sale | Loyalty points after a sale | Stock |
|---|---|---|---|
| ![](docs/screenshots/02-salesperson-dashboard.png) | ![](docs/screenshots/04-new-sale.png) | ![](docs/screenshots/05-rewards-after-sale.png) | ![](docs/screenshots/09-stock.png) |

![Admin dashboard](docs/screenshots/10-admin-dashboard.png)

---

## The problem

- 2 field salespeople cover ~80 small shops; sales were written on paper and totalled by hand.
- Shops rarely reordered, half the orders were ≤ ₹200, and prices varied by salesperson.
- No view of who sold what, where, or which days had no sales at all.

## What the app does

### For salespeople (phone)
- **📊 Monthly dashboard:** sales vs last month, days with sales, **zero-sale days**, and a colour-coded calendar.
- **🛒 Sale entry per shop visit:** search or add a shop on the spot; price + quantity for each product in 3 pack sizes;
  the last price used at that shop is pre-filled.
- **🌶️ मसाला परिवार (Masala Parivaar) loyalty programme:** shops earn points per pack; *"अभी लेना है या बचाना है?"*
  (take a free pack now or keep saving for a gift); a one-tap **WhatsApp** message with the shop's balance.
- **📦 Stock:** each salesperson sets their own full level per product; stock left updates with every sale and free pack;
  **Refill** / **＋** per product; overselling warnings on the sale screen.
- **🏪 Shop history:** every sale by every salesperson, what the shop usually buys and the last price charged.
- **💰 Expenses:** petrol, food, travel… with admin-managed categories.

### For the admin (owner)
- Dashboard with filters (dates, salesperson, shop): revenue, units per size, top shops, products with average
  selling price, revenue by day, every sale, **CSV export**.
- **📅 Team calendar:** every salesperson's month side by side, with zero-sale days.
- Loyalty admin: gift catalogue, editable reward rules, gift approvals (requested → approved → delivered),
  point adjustments with a required reason (audit log), **reward cost as % of sales**.
- Manage products, shops (fix names, remove duplicates), users and expense categories.

| Shop history | Team calendar | Rewards admin |
|---|---|---|
| ![](docs/screenshots/07-shop-history.png) | ![](docs/screenshots/11-admin-team-calendar.png) | ![](docs/screenshots/12-admin-rewards.png) |

---

## Architecture

![System architecture](docs/arch-1-system.png)

- **Backend:** Flask JSON API (`app.py`, 55 endpoints) with role-based access (admin / salesperson).
- **Database:** SQLite, 14 tables ([diagram](docs/arch-2-tables.png)), automatic schema migrations on startup.
- **Frontend:** a single-page app in plain HTML/CSS/JS (`static/`), mobile-first at 360 px, bilingual.
- **Points engine:** `rewards.py`, a pure module with 22 unit tests.

Full diagrams: [`docs/architecture.html`](docs/architecture.html) (system, data model, sale flow, stored vs calculated).

### Design decisions
- **Derived balances, never stored:** loyalty points and stock are always recomputed from the underlying
  records (orders, redemptions, adjustments, stock moves), so they can't drift. Any change that would make
  a shop's points negative (e.g. editing a sale whose points were already redeemed) is rejected inside the transaction.
- **Money as integers (paise)** to avoid floating-point rounding.
- **Points rules** (earn per size, free-pack cost, saving bonus, 12-month expiry, oldest points used first) live in one
  admin-editable config; changing them recalculates history and is refused if it would push any shop negative.
- **Soft deletes** for products, shops and categories, so historical reports stay correct.
- **Security:** salted password hashes, login rate-limiting, HttpOnly/SameSite session cookies, a CSRF guard header,
  server-side role checks, HTML escaping, and protection against formula injection in CSV exports.

---

## Run it locally

```bash
git clone https://github.com/<your-username>/kcm-sales-portal.git
cd kcm-sales-portal
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python demo_seed.py                                     # creates demo.db with sample data
DATABASE=demo.db python app.py                          # Windows: set DATABASE=demo.db && python app.py
```

Open http://127.0.0.1:5000. Demo logins: **admin / admin123**, **rahul / demo123**, **amit / demo123**.

Run the tests:

```bash
python -m unittest -v test_rewards
```

Production: `waitress-serve --port=8000 app:app` behind HTTPS with `COOKIE_SECURE=1` (deployed on PythonAnywhere).

## Project structure

```
app.py            Flask API: auth, sales, shops, rewards, stock, expenses, reports
rewards.py        loyalty points engine (earn, saving bonus, FIFO expiry, no negatives)
test_rewards.py   unit tests for the points engine
schema.sql        database schema (14 tables)
demo_seed.py      generates a demo database with sample data
static/           index.html, app.js, style.css (single-page frontend)
docs/             architecture diagrams and screenshots
```

## Tech stack
Python 3 · Flask · SQLite · Waitress · JavaScript (ES2020) · HTML5 · CSS3 · PythonAnywhere

*All data in the screenshots and demo database is sample data.*

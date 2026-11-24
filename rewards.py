"""Masala Parivaar points engine (pure Python, no database access).

A shop's points are never stored as a single number. They are always recomputed from three
kinds of events, so the balance can never drift out of step with the records:

  * earn    - an order (points = packs bought x points per size)
  * adjust  - a manual admin correction (+/-), always with a reason
  * redeem  - a free pack or a gift (minus points)

Rules (values come from the reward config, see DEFAULT_CONFIG):

  * Earning: each pack earns config["earn"][size] points. Sizes combine into one balance.
  * Expiry: every batch of points (each earn, bonus and positive adjustment) expires
    `expiry_months` after its date. Points are always used and expired OLDEST FIRST.
    expiry_months = 0 means points never expire.
  * Saving bonus: S = points earned from orders since the shop's last redemption.
    Every time S passes another multiple of `bonus_every`, the shop gets `bonus_points`.
    Any redemption resets S to 0. Bonus points and adjustments do not count towards S,
    and expiry does not reduce S.
  * The balance can never go below zero. compute() reports the first event that would
    make it negative, so callers can refuse that change.

Events on the same date are processed in this order: earn, adjust, redeem (so a shop can
redeem the points from the order it just placed), then by their sequence number.
"""
from collections import deque
from datetime import date, timedelta

DEFAULT_CONFIG = {
    "earn": {"SMALL": 1, "BIG": 2, "20 BIG": 4},        # points per pack bought
    "pack_cost": {"SMALL": 0, "BIG": 10, "20 BIG": 20},  # points for one free pack (0 = not offered)
    "bonus_every": 20,     # saving bonus: every N points saved without redeeming ...
    "bonus_points": 5,     # ... earns this many extra points
    "expiry_months": 12,   # points expire this many months after they were earned (0 = never)
    "start_date": None,    # orders on/after this date earn points (set on first install)
    "country_code": "91",  # for WhatsApp links when a shop phone has no country code
}

KIND_ORDER = {"earn": 0, "adjust": 1, "redeem": 2}


def add_months(d, months):
    """Same day N months later, clamped to the end of shorter months (31 Jan + 1 = 28/29 Feb)."""
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, min(d.day, day))
        except ValueError:
            continue
    raise ValueError("bad date")


def _d(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def order_points(items, config):
    """items: iterable of (size, quantity). Unknown sizes earn nothing."""
    earn = config["earn"]
    return sum(int(earn.get(size, 0)) * int(qty) for size, qty in items)


def compute(events, config, as_of=None):
    """Replay a shop's events and return its points statement.

    events: list of dicts with keys
        date   (date or 'YYYY-MM-DD'), kind ('earn' | 'adjust' | 'redeem'),
        points (int, always positive for earn/redeem; +/- for adjust),
        seq    (int, tie-breaker), and optional 'ref' / 'label' (passed through to history).
    """
    as_of = _d(as_of) if as_of else date.today()
    months = int(config.get("expiry_months") or 0)
    every = int(config.get("bonus_every") or 0)
    bonus_pts = int(config.get("bonus_points") or 0)

    lots = deque()          # [expires_on (date|None), remaining points], oldest first
    history = []
    totals = {"earned": 0, "bonus": 0, "redeemed": 0, "expired": 0, "adjusted": 0}
    saved, bonus_steps = 0, 0
    error = None

    def balance():
        return sum(lot[1] for lot in lots)

    def add_lot(on, pts):
        lots.append([add_months(on, months) if months else None, pts])

    def take(pts):
        """Remove pts oldest first. Returns the shortfall (0 if there were enough points)."""
        while pts and lots:
            use = min(pts, lots[0][1])
            lots[0][1] -= use
            pts -= use
            if lots[0][1] == 0:
                lots.popleft()
        return pts

    def expire_until(day):
        # A batch with expires_on <= day is no longer valid on `day`.
        while lots and lots[0][0] is not None and lots[0][0] <= day:
            exp_on, pts = lots.popleft()
            if pts:
                totals["expired"] += pts
                history.append({"date": exp_on.isoformat(), "kind": "expire", "points": -pts,
                                "balance": balance(), "label": "Points expired"})

    ordered = sorted(events, key=lambda e: (_d(e["date"]), KIND_ORDER[e["kind"]], e.get("seq", 0)))
    for e in ordered:
        day, kind, pts = _d(e["date"]), e["kind"], int(e["points"])
        expire_until(day)
        entry = {"date": day.isoformat(), "kind": kind, "ref": e.get("ref"), "label": e.get("label")}
        if kind == "earn":
            if pts <= 0:
                continue
            add_lot(day, pts)
            totals["earned"] += pts
            saved += pts
            history.append({**entry, "points": pts, "balance": balance()})
            if every and bonus_pts:
                steps = saved // every
                if steps > bonus_steps:
                    b = (steps - bonus_steps) * bonus_pts
                    bonus_steps = steps
                    add_lot(day, b)
                    totals["bonus"] += b
                    history.append({"date": day.isoformat(), "kind": "bonus", "points": b, "balance": balance(),
                                    "label": f"Saving bonus ({steps * every} points saved)"})
        elif kind == "adjust":
            if pts > 0:
                add_lot(day, pts)
            elif pts < 0 and take(-pts) and error is None:
                error = {"event": e, "message": "Adjustment is more than the shop's points"}
            totals["adjusted"] += pts
            history.append({**entry, "points": pts, "balance": balance()})
        elif kind == "redeem":
            short = take(pts)
            if short and error is None:
                error = {"event": e, "message": f"Not enough points: needs {pts}, has {pts - short}"}
            totals["redeemed"] += pts
            saved, bonus_steps = 0, 0
            history.append({**entry, "points": -pts, "balance": balance()})

    expire_until(as_of)
    soon = as_of + timedelta(days=30)
    expiring = [(lot[0], lot[1]) for lot in lots if lot[0] is not None and lot[0] <= soon and lot[1]]
    return {
        "balance": balance(),
        "totals": totals,
        "saved_since_redeem": saved,
        "next_bonus_in": (every - saved % every) if every and bonus_pts else None,
        "expiring_30_days": sum(p for _, p in expiring),
        "next_expiry": min((d for d, _ in expiring), default=None),
        "history": history,
        "ok": error is None,
        "error": error,
    }

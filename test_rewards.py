"""Tests for the Masala Parivaar points engine.  Run:  python -m unittest -v test_rewards"""
import unittest
from datetime import date

from rewards import DEFAULT_CONFIG, add_months, compute, order_points

CFG = {**DEFAULT_CONFIG, "start_date": "2026-01-01"}


def earn(d, pts, seq=0):
    return {"date": d, "kind": "earn", "points": pts, "seq": seq}


def redeem(d, pts, seq=0):
    return {"date": d, "kind": "redeem", "points": pts, "seq": seq}


def adjust(d, pts, seq=0):
    return {"date": d, "kind": "adjust", "points": pts, "seq": seq}


AS_OF = "2026-06-01"


class OrderPoints(unittest.TestCase):
    def test_sizes_combine(self):
        # 10 SMALL = 10 points = one free BIG; 1 BIG = 2 SMALL; 1 x 20 BIG = 4 SMALL
        self.assertEqual(order_points([("SMALL", 10)], CFG), 10)
        self.assertEqual(order_points([("SMALL", 6), ("BIG", 2)], CFG), 10)
        self.assertEqual(order_points([("BIG", 10)], CFG), 20)          # = one free 20 BIG
        self.assertEqual(order_points([("20 BIG", 3), ("UNKNOWN", 5)], CFG), 12)


class SavingBonus(unittest.TestCase):
    def test_no_bonus_below_20(self):
        r = compute([earn("2026-02-01", 19)], CFG, AS_OF)
        self.assertEqual(r["balance"], 19)
        self.assertEqual(r["totals"]["bonus"], 0)
        self.assertEqual(r["next_bonus_in"], 1)

    def test_saving_20_gives_2_5_big(self):
        r = compute([earn("2026-02-01", 10), earn("2026-02-08", 10)], CFG, AS_OF)
        self.assertEqual(r["balance"], 25)                      # 25 points = 2.5 free BIG
        self.assertEqual(r["totals"]["bonus"], 5)

    def test_bonus_for_every_20(self):
        # saved 10 -> 1 BIG, 20 -> 2.5, 30 -> 3.5, 40 -> 5, 60 -> 7.5 (BIG costs 10 points)
        for saved, points in [(10, 10), (20, 25), (30, 35), (40, 50), (60, 75)]:
            r = compute([earn("2026-02-01", saved)], CFG, AS_OF)
            self.assertEqual(r["balance"], points, saved)

    def test_one_big_order_crossing_several_steps(self):
        r = compute([earn("2026-02-01", 45)], CFG, AS_OF)
        self.assertEqual(r["totals"]["bonus"], 10)
        self.assertEqual(r["next_bonus_in"], 15)

    def test_redemption_resets_saving(self):
        r = compute([earn("2026-02-01", 15), redeem("2026-02-02", 10), earn("2026-02-03", 15)], CFG, AS_OF)
        # 15 saved, redeem (S=0), then 15 more: S=15 -> no bonus yet
        self.assertEqual(r["totals"]["bonus"], 0)
        self.assertEqual(r["balance"], 20)
        self.assertEqual(r["saved_since_redeem"], 15)

    def test_bonus_and_adjustments_do_not_count_toward_saving(self):
        r = compute([earn("2026-02-01", 20), adjust("2026-02-02", 30), earn("2026-02-03", 19)], CFG, AS_OF)
        self.assertEqual(r["saved_since_redeem"], 39)
        self.assertEqual(r["totals"]["bonus"], 5)               # only the first 20; 39 < 40

    def test_expiry_does_not_reduce_saving(self):
        r = compute([earn("2025-01-10", 15), earn("2026-03-01", 5)], CFG, AS_OF)
        # the 15 expired on 2026-01-10 but still count as saved: S=20 -> bonus
        self.assertEqual(r["totals"]["expired"], 15)
        self.assertEqual(r["totals"]["bonus"], 5)
        self.assertEqual(r["balance"], 10)

    def test_bonus_can_be_switched_off(self):
        r = compute([earn("2026-02-01", 40)], {**CFG, "bonus_points": 0}, AS_OF)
        self.assertEqual(r["balance"], 40)
        self.assertIsNone(r["next_bonus_in"])


class Expiry(unittest.TestCase):
    def test_points_expire_after_12_months(self):
        r = compute([earn("2025-05-15", 8)], CFG, AS_OF)
        self.assertEqual(r["balance"], 0)
        self.assertEqual(r["totals"]["expired"], 8)

    def test_still_valid_day_before_expiry(self):
        r = compute([earn("2025-06-02", 8)], CFG, "2026-06-01")
        self.assertEqual(r["balance"], 8)
        self.assertEqual(r["expiring_30_days"], 8)
        self.assertEqual(r["next_expiry"], date(2026, 6, 2))

    def test_redeem_uses_oldest_points_first(self):
        events = [earn("2025-07-01", 10), earn("2026-01-01", 10), redeem("2026-02-01", 12)]
        r = compute(events, {**CFG, "bonus_points": 0}, "2026-07-02")
        # redeem took all 10 old + 2 new; old batch would have expired 2026-07-01 anyway
        self.assertEqual(r["balance"], 8)
        self.assertEqual(r["totals"]["expired"], 0)

    def test_oldest_expire_first_keep_newer(self):
        events = [earn("2025-03-01", 6), earn("2025-09-01", 4)]
        r = compute(events, CFG, "2026-04-01")
        self.assertEqual(r["balance"], 4)
        self.assertEqual(r["totals"]["expired"], 6)

    def test_bonus_points_expire_too(self):
        r = compute([earn("2025-04-01", 20)], CFG, "2026-04-02")
        self.assertEqual(r["totals"]["expired"], 25)
        self.assertEqual(r["balance"], 0)

    def test_never_expire_when_zero(self):
        r = compute([earn("2020-01-01", 7)], {**CFG, "expiry_months": 0}, AS_OF)
        self.assertEqual(r["balance"], 7)

    def test_add_months_clamps_month_end(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2024, 2, 29), 12), date(2025, 2, 28))
        self.assertEqual(add_months(date(2026, 11, 9), 12), date(2027, 11, 9))


class Redemption(unittest.TestCase):
    def test_cannot_go_negative(self):
        r = compute([earn("2026-02-01", 9), redeem("2026-02-01", 10)], CFG, AS_OF)
        self.assertFalse(r["ok"])
        self.assertIn("Not enough points", r["error"]["message"])

    def test_exact_balance_ok(self):
        r = compute([earn("2026-02-01", 10), redeem("2026-02-01", 10)], CFG, AS_OF)
        self.assertTrue(r["ok"])
        self.assertEqual(r["balance"], 0)

    def test_same_day_order_then_redeem(self):
        # redeem is processed after the earn on the same date, regardless of list order
        r = compute([redeem("2026-02-01", 10, seq=1), earn("2026-02-01", 10, seq=99)], CFG, AS_OF)
        self.assertTrue(r["ok"])

    def test_expired_points_cannot_be_redeemed(self):
        r = compute([earn("2025-01-01", 10), redeem("2026-02-01", 10)], CFG, AS_OF)
        self.assertFalse(r["ok"])

    def test_negative_adjustment_cannot_exceed_balance(self):
        r = compute([earn("2026-02-01", 5), adjust("2026-02-02", -6)], CFG, AS_OF)
        self.assertFalse(r["ok"])

    def test_history_running_balance(self):
        r = compute([earn("2026-02-01", 12), redeem("2026-02-02", 10), adjust("2026-02-03", 3)], CFG, AS_OF)
        self.assertEqual([(h["kind"], h["points"], h["balance"]) for h in r["history"]],
                         [("earn", 12, 12), ("redeem", -10, 2), ("adjust", 3, 5)])


if __name__ == "__main__":
    unittest.main()

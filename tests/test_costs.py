"""The Indian charge stack, in rupees, per leg and round trip."""

import unittest

import _load  # noqa: F401
from indkit import costs


class TestLegs(unittest.TestCase):
    def test_buy_leg_components(self):
        buy = costs.leg(120, 65, "NSE", "buy", on="2026-11-09")
        self.assertEqual(buy["turnover"], 7800.0)
        self.assertEqual(buy["brokerage"], 20.0)
        self.assertEqual(buy["stt"], 0.0)                 # STT is sell-side only
        self.assertAlmostEqual(buy["exchange"], 2.73, places=2)
        self.assertAlmostEqual(buy["stamp"], 0.23, places=2)
        self.assertAlmostEqual(buy["gst"], 4.09, places=2)
        self.assertAlmostEqual(buy["total"], 27.08, places=2)

    def test_sell_leg_pays_stt_from_april_2026(self):
        sell = costs.leg(150, 65, "NSE", "sell", on="2026-11-09")
        self.assertEqual(sell["turnover"], 9750.0)
        self.assertAlmostEqual(sell["stt"], 14.62, places=1)   # 0.15% of premium
        self.assertEqual(sell["stamp"], 0.0)

    def test_stt_rate_before_april_2026(self):
        sell = costs.leg(150, 65, "NSE", "sell", on="2026-03-31")
        self.assertAlmostEqual(sell["stt"], 9.75, places=2)    # 0.10% of premium

    def test_bse_exchange_charge(self):
        buy_nse = costs.leg(100, 20, "NSE", "buy", on="2026-11-09")
        buy_bse = costs.leg(100, 20, "BSE", "buy", on="2026-11-09")
        self.assertLess(buy_bse["exchange"], buy_nse["exchange"])


class TestRoundTrip(unittest.TestCase):
    def test_one_nifty_lot_round_trip(self):
        trip = costs.round_trip(120, 150, 1, 65, "NSE", on="2026-11-09")
        self.assertEqual(trip["units"], 65)
        self.assertAlmostEqual(trip["total"], 69.36, places=2)
        self.assertAlmostEqual(trip["per_unit"], 1.067, places=3)
        self.assertAlmostEqual(trip["breakeven_move"], 1.067, places=3)
        # about 0.9% of the premium paid - which is why a Rs 4 stop is not a stop
        self.assertGreater(trip["pct_of_entry_premium"], 0.8)
        self.assertLess(trip["pct_of_entry_premium"], 1.1)

    def test_costs_scale_with_lots(self):
        one = costs.round_trip(120, 150, 1, 65, "NSE", on="2026-11-09")
        four = costs.round_trip(120, 150, 4, 65, "NSE", on="2026-11-09")
        self.assertEqual(four["units"], 260)
        # brokerage is flat per order, the ad-valorem charges scale with size
        self.assertGreater(four["total"], 1.8 * one["total"])
        self.assertLess(four["total"], 4 * one["total"])
        self.assertAlmostEqual(four["sell"]["stt"], 4 * one["sell"]["stt"], places=1)

    def test_config_overrides_are_merged_not_replaced(self):
        trip = costs.round_trip(120, 150, 1, 65, "NSE",
                                rates={"charges": {"brokerage_per_order": 0}},
                                on="2026-11-09")
        self.assertEqual(trip["buy"]["brokerage"], 0.0)
        self.assertGreater(trip["buy"]["exchange"], 0)     # the rest of the stack survives

    def test_exercise_stt_is_on_intrinsic(self):
        # a deep ITM option kept to settlement: STT on 500 points of intrinsic
        exercise = costs.exercise(25550, 25050, 1, 65)
        self.assertEqual(exercise["intrinsic"], 500.0)
        self.assertAlmostEqual(exercise["stt_on_intrinsic"], 48.75, places=2)
        self.assertIn("intrinsic", exercise["note"])


if __name__ == "__main__":
    unittest.main()

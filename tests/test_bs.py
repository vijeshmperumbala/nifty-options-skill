"""Black-Scholes, the greeks, and the premium <-> index translation."""

import datetime as dt
import unittest

import _load
from indkit import bs

S, K, T, R, V, Q = 25044.65, 25050.0, 8 / 365, 0.065, 0.174, 0.012


class TestPricing(unittest.TestCase):
    def test_put_call_parity(self):
        call = bs.price(S, K, T, R, V, "CE", Q)
        put = bs.price(S, K, T, R, V, "PE", Q)
        import math
        self.assertAlmostEqual(call - put, S * math.exp(-Q * T) - K * math.exp(-R * T), places=6)

    def test_premium_is_positive_and_atm_greeks_are_sane(self):
        call = bs.greeks(S, K, T, R, V, "CE", Q)
        self.assertGreater(call["price"], 0)
        self.assertAlmostEqual(call["delta"], 0.5, delta=0.08)
        self.assertGreater(call["gamma"], 0)
        self.assertLess(call["theta"], 0)
        self.assertGreater(call["vega"], 0)

        put = bs.greeks(S, K, T, R, V, "PE", Q)
        self.assertAlmostEqual(put["delta"], -0.5, delta=0.08)
        self.assertLess(put["theta"], 0)          # a bought option bleeds either way

    def test_expiry_value_is_intrinsic(self):
        self.assertAlmostEqual(bs.price(25200, 25050, 0, R, V, "CE"), 150.0, places=6)
        self.assertAlmostEqual(bs.price(25200, 25050, 0, R, V, "PE"), 0.0, places=6)

    def test_deep_itm_delta_approaches_one(self):
        self.assertGreater(bs.greeks(S, S - 4000, T, R, V, "CE")["delta"], 0.95)
        self.assertLess(bs.greeks(S, S + 4000, T, R, V, "CE")["delta"], 0.05)

    def test_implied_vol_round_trip(self):
        for sigma in (0.10, 0.174, 0.35):
            for right in ("CE", "PE"):
                price = bs.price(S, K, T, R, sigma, right, Q)
                solved = bs.implied_vol(price, S, K, T, R, right, Q)
                self.assertIsNotNone(solved)
                self.assertAlmostEqual(solved, sigma, places=4)

    def test_implied_vol_refuses_impossible_prices(self):
        self.assertIsNone(bs.implied_vol(None, S, K, T, R, "CE"))
        self.assertIsNone(bs.implied_vol(0.0, S, K, T, R, "CE"))
        self.assertIsNone(bs.implied_vol(26000.0, S, K, T, R, "CE"))  # above the arbitrage bound
        # a call priced under its intrinsic value has no implied vol at all
        self.assertIsNone(bs.implied_vol(1000.0, 25044.65, 24000, T, R, "CE"))


class TestTranslation(unittest.TestCase):
    def test_years_to_expiry_shrinks_and_ends(self):
        now = dt.datetime(2026, 11, 9, 10, 45)
        early = bs.years_to_expiry(now, dt.date(2026, 11, 17))
        later = bs.years_to_expiry(dt.datetime(2026, 11, 17, 10, 45), dt.date(2026, 11, 17))
        self.assertGreater(early, later)
        self.assertGreater(later, 0)
        # after the 15:30 settlement there is no time value left
        self.assertEqual(bs.years_to_expiry(dt.datetime(2026, 11, 17, 15, 45), dt.date(2026, 11, 17)), 0.0)

    def test_delta_maps_index_moves_to_premium(self):
        put = bs.greeks(S, 24850, T, R, V, "PE", Q)
        # a 200 point fall in the index is worth roughly delta x 200 on the premium
        moved = bs.premium_for_index_move(put["price"], S, -200, put["delta"], put["gamma"])
        self.assertGreater(moved - put["price"], 80)
        self.assertLess(moved - put["price"], 130)

    def test_strike_for_delta(self):
        strike, delta = bs.strike_for_delta(S, T, R, V, "CE", 0.35, q=Q, step=50)
        self.assertAlmostEqual(abs(delta), 0.35, delta=0.05)
        self.assertGreater(strike, S)


if __name__ == "__main__":
    unittest.main()

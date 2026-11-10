"""The opening-range signal, the contract choice, and the premium translation."""

import datetime as dt
import unittest

import _load
from indkit import clock, feed, instruments, workflow

DAY = dt.date(2026, 11, 9)
SESSION = clock.now_ist("2026-11-09 15:30")


class TestOpeningRange(unittest.TestCase):
    def test_range_and_first_break(self):
        bars = feed.session_bars("NIFTY", day=DAY, now=SESSION)
        self.assertEqual(len(bars), 75)
        rng = feed.opening_range(bars, 15, "5m")
        self.assertAlmostEqual(rng["high"], 25081.0, places=2)
        self.assertAlmostEqual(rng["low"], 24899.40, places=2)
        self.assertEqual(rng["bars"], 3)

        signal = workflow.orb_signal(bars, 15, "5m", now=SESSION)
        self.assertEqual(signal["status"], "triggered")
        self.assertEqual(signal["direction"], "PE")          # the first break was DOWN
        self.assertEqual(signal["at"].strftime("%H:%M"), "10:40")
        self.assertAlmostEqual(signal["price"], 24845.75, places=2)

    def test_waiting_before_the_break(self):
        bars = feed.session_bars("NIFTY", day=DAY, now=clock.now_ist("2026-11-09 10:30"))
        signal = workflow.orb_signal(bars, 15, "5m", now=clock.now_ist("2026-11-09 10:30"))
        self.assertEqual(signal["status"], "waiting")
        self.assertGreater(signal["to_high"], 0)
        self.assertGreater(signal["to_low"], 0)

    def test_range_still_forming(self):
        bars = feed.session_bars("NIFTY", day=DAY, now=clock.now_ist("2026-11-09 09:25"))
        signal = workflow.orb_signal(bars, 15, "5m", now=clock.now_ist("2026-11-09 09:25"))
        self.assertEqual(signal["status"], "range-forming")

    def test_cutoff_stops_late_breaks(self):
        bars = feed.session_bars("NIFTY", day=DAY, now=SESSION)
        signal = workflow.orb_signal(bars, 15, "5m", cutoff=dt.time(10, 30), now=SESSION)
        self.assertEqual(signal["status"], "waiting")        # the 10:40 break is after the cutoff


class TestContractChoice(unittest.TestCase):
    def test_atm_put_from_the_breakdown(self):
        info = workflow.pick_contract("NIFTY", 24845.75, config={}, now=_load.SESSION,
                                      direction="PE", chain_source="model")
        self.assertEqual(info["right"], "PE")
        self.assertEqual(info["strike"], 24850.0)
        self.assertEqual(info["lot"], 65)
        self.assertEqual(info["days_to_expiry"], 8)
        self.assertEqual(info["series"], "weekly")
        self.assertEqual(info["premium_source"], "model")
        self.assertLess(info["delta"], 0)
        self.assertGreater(info["premium"], 100)

    def test_offset_steps_move_the_strike(self):
        near = workflow.pick_contract("NIFTY", 24845.75, config={}, now=_load.SESSION,
                                      direction="PE", offset=0, chain_source="model")
        far = workflow.pick_contract("NIFTY", 24845.75, config={}, now=_load.SESSION,
                                     direction="PE", offset=2, chain_source="model")
        self.assertEqual(far["strike"], near["strike"] - 100)
        self.assertLess(far["premium"], near["premium"])

    def test_expiry_day_is_skipped_after_the_close(self):
        master = instruments.Master.load({}, now=_load.SESSION)
        expiry = workflow.tradable_expiry(master, "NIFTY", _load.SESSION, {})
        self.assertEqual(expiry, dt.date(2026, 11, 17))

    def test_expiry_day_is_skipped_by_default_even_intraday(self):
        master = instruments.Master.load({}, now=_load.SESSION)
        expiry = workflow.tradable_expiry(master, "NIFTY", clock.now_ist("2026-11-09 10:30"), {})
        self.assertEqual(expiry, dt.date(2026, 11, 17))       # 9 Nov settles today
        allowed = workflow.tradable_expiry(master, "NIFTY", clock.now_ist("2026-11-09 10:30"),
                                           {"allow_expiry_day_entries": True})
        self.assertEqual(allowed, dt.date(2026, 11, 9))

    def test_min_premium_floor_refuses_a_cheap_strike(self):
        info = workflow.pick_contract("NIFTY", 24845.75, config={"min_premium": 150},
                                      now=_load.SESSION, direction="PE", offset=10, chain_source="model")
        refusal = workflow.refusal_reason(info, {"min_premium": 150})
        self.assertIn("floor", refusal)


class TestTranslation(unittest.TestCase):
    def setUp(self):
        self.info = workflow.pick_contract("NIFTY", 24845.75, config={}, now=_load.SESSION,
                                           direction="PE", chain_source="model")

    def test_premium_round_trip_through_the_index(self):
        target = self.info["premium"] * 1.4
        level = workflow.index_for_premium(self.info, target, {}, _load.SESSION)
        self.assertIsNotNone(level)
        self.assertLess(level, self.info["spot"])            # a put gains as the index falls
        back = workflow.premium_at_index(self.info, level, {}, _load.SESSION)
        self.assertAlmostEqual(back, target, delta=1.0)

    def test_risk_math_includes_charges(self):
        maths = workflow.risk_math(246.08, 178.34, 347.69, 1, 65, "NSE", _load.SESSION, {})
        self.assertEqual(maths["units"], 65)
        self.assertAlmostEqual(maths["gross_risk"], 4403.10, places=2)
        self.assertGreater(maths["net_risk"], maths["gross_risk"])       # charges add to the loss
        self.assertLess(maths["net_reward"], maths["gross_reward"])
        self.assertLess(maths["reward_risk"], 1.5)                       # costs shave the R
        self.assertGreater(maths["reward_risk"], 1.25)

    def test_premium_source_is_always_labelled(self):
        self.assertIn("MODELLED", workflow.describe_premium_source(self.info))


if __name__ == "__main__":
    unittest.main()

"""The contract master: lots, expiries, series, strikes, broker symbols."""

import datetime as dt
import unittest

import _load
from indkit import instruments


class TestMaster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.master = instruments.Master.load({}, now=_load.SESSION)

    def test_lot_sizes_come_from_the_exchange(self):
        self.assertEqual(self.master.lot("NIFTY"), 65)
        self.assertEqual(self.master.lot("BANKNIFTY"), 30)
        self.assertEqual(self.master.lot("SENSEX"), 20)

    def test_expiry_series(self):
        nifty = self.master.expiries("NIFTY", dt.date(2026, 11, 9))
        # 9 Nov is the pulled-forward weekly (10 Nov is a holiday); 17 Nov is the
        # next weekly; 23 Nov is the monthly because 24 Nov is a holiday.
        self.assertIn(dt.date(2026, 11, 9), nifty)
        self.assertIn(dt.date(2026, 11, 17), nifty)
        self.assertEqual(self.master.series_kind("NIFTY", dt.date(2026, 11, 17)), "weekly")
        self.assertEqual(self.master.series_kind("NIFTY", dt.date(2026, 11, 23)), "monthly")

    def test_sensex_expires_on_thursday(self):
        expiry = self.master.nearest_expiry("SENSEX", dt.date(2026, 11, 9))
        self.assertEqual(expiry, dt.date(2026, 11, 12))
        self.assertEqual(expiry.strftime("%A"), "Thursday")

    def test_banknifty_is_monthly_only(self):
        expiries = self.master.expiries("BANKNIFTY", dt.date(2026, 11, 9))
        self.assertGreaterEqual(len(expiries), 1)
        for expiry in expiries:
            self.assertEqual(self.master.series_kind("BANKNIFTY", expiry), "monthly")

    def test_strike_steps(self):
        self.assertEqual(self.master.step("NIFTY", dt.date(2026, 11, 17)), 50.0)
        self.assertEqual(self.master.step("SENSEX", dt.date(2026, 11, 12)), 100.0)
        self.assertEqual(self.master.step("BANKNIFTY", dt.date(2026, 11, 23)), 100.0)

    def test_atm_and_snapping(self):
        contract = self.master.atm("NIFTY", 25044.65, dt.date(2026, 11, 17), "CE")
        self.assertIsNotNone(contract)
        self.assertEqual(contract.strike, 25050.0)
        self.assertEqual(contract.lot, 65)
        self.assertIn("CE", contract.symbol)
        self.assertEqual(self.master.snap_strike("NIFTY", dt.date(2026, 11, 17), 25061), 25050.0)

    def test_atm_put_steps_down(self):
        call = self.master.atm("NIFTY", 25044.65, dt.date(2026, 11, 17), "CE", offset_steps=1)
        put = self.master.atm("NIFTY", 25044.65, dt.date(2026, 11, 17), "PE", offset_steps=1)
        self.assertEqual(call.strike, 25100.0)
        self.assertEqual(put.strike, 25000.0)

    def test_get_and_symbol_lookup(self):
        contract = self.master.get("NIFTY", dt.date(2026, 11, 17), 25000, "PE")
        self.assertIsNotNone(contract)
        self.assertTrue(contract.symbol.startswith("NIFTY"))
        self.assertEqual(self.master.by_symbol(contract.symbol), contract)
        self.assertIsNone(self.master.get("NIFTY", dt.date(2026, 11, 17), 12345, "PE"))

    def test_context_reports_structure(self):
        context = self.master.context("NIFTY", _load.SESSION)
        self.assertEqual(context["lot"], 65)
        self.assertEqual(context["exchange"], "NSE")
        # context reports the NEAREST expiry; skipping today's is /trader-entry's job
        self.assertEqual(context["expiry"], dt.date(2026, 11, 9))
        self.assertEqual(context["strike_step"], 50.0)

    def test_describe_is_human_readable(self):
        contract = self.master.get("NIFTY", dt.date(2026, 11, 17), 25000, "CE")
        text = contract.describe(dt.date(2026, 11, 9))
        self.assertIn("25000", text)
        self.assertIn("lot 65", text)


if __name__ == "__main__":
    unittest.main()

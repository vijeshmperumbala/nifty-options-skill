"""The option chain: CSV import, the modelled fallback, and the analytics."""

import os
import tempfile
import unittest

import _load
from indkit import chain

SPOT = 25044.65
EXPIRY = "2026-11-17"


def write_csv(path, rows, header="strike,ce_ltp,ce_oi,ce_iv,pe_ltp,pe_oi,pe_iv"):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(header + "\n")
        for row in rows:
            handle.write(",".join(str(value) for value in row) + "\n")


class TestModelled(unittest.TestCase):
    def test_model_chain_brackets_the_spot(self):
        model = chain.load("NIFTY", spot=SPOT, expiry=_date(EXPIRY), config={},
                           now=_load.SESSION, source="model")
        self.assertEqual(model.source, "model")
        self.assertFalse(model.real)
        self.assertGreater(len(model.rows), 20)
        self.assertEqual(model.nearest_strike(SPOT), 25050.0)
        call = model.premium(25050, "CE")
        put = model.premium(25050, "PE")
        self.assertGreater(call, 0)
        self.assertGreater(put, 0)
        # ATM call and put are close to each other, put slightly cheaper with carry
        self.assertLess(abs(call - put), 60)

    def test_model_refuses_open_interest_analytics(self):
        model = chain.load("NIFTY", spot=SPOT, expiry=_date(EXPIRY), config={},
                           now=_load.SESSION, source="model")
        for call in (model.pcr, model.max_pain, model.oi_walls, model.skew):
            with self.assertRaises(chain.ChainError):
                call()
        # but the model still gives an expected move
        self.assertGreater(model.expected_move(_load.SESSION)["points"], 100)

    def test_straddle_and_expected_move(self):
        model = chain.load("NIFTY", spot=SPOT, expiry=_date(EXPIRY), config={},
                           now=_load.SESSION, source="model")
        straddle = model.straddle(_load.SESSION)
        self.assertAlmostEqual(straddle["total"], straddle["call"] + straddle["put"], places=2)
        self.assertGreater(straddle["expected_move_pct"], 0.5)


class TestCsv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "chain.csv")
        rows = []
        for offset in range(-5, 6):
            strike = 25050 + offset * 50
            call = max(5, 250 - offset * 45)
            put = max(5, 250 + offset * 45)
            # put-heavy open interest below the money, call-heavy above
            call_oi = 100000 + max(0, offset) * 400000
            put_oi = 100000 + max(0, -offset) * 400000
            rows.append((strike, call, call_oi, 14 + abs(offset) * 0.5,
                         put, put_oi, 14 + abs(offset) * 0.5))
        write_csv(self.path, rows)

    def test_csv_import(self):
        imported = chain.load("NIFTY", spot=SPOT, expiry=_date(EXPIRY), config={},
                              now=_load.SESSION, source="csv", path=self.path)
        self.assertTrue(imported.real)
        self.assertEqual(imported.source, "csv")
        self.assertEqual(len(imported.rows), 11)
        self.assertAlmostEqual(imported.premium(25050, "CE"), 250.0)

    def test_pcr_and_walls(self):
        imported = chain.load("NIFTY", spot=SPOT, expiry=_date(EXPIRY), config={},
                              now=_load.SESSION, source="csv", path=self.path)
        pcr = imported.pcr()
        self.assertGreater(pcr["ratio"], 0)
        self.assertIn(pcr["bias"], ("put-heavy", "call-heavy", "balanced"))
        walls = imported.oi_walls(count=2)
        self.assertEqual(walls["call_walls"][0], 25300.0)   # call OI piles up at the highest strike
        self.assertEqual(walls["put_walls"][0], 24800.0)

    def test_max_pain_sits_between_the_walls(self):
        imported = chain.load("NIFTY", spot=SPOT, expiry=_date(EXPIRY), config={},
                              now=_load.SESSION, source="csv", path=self.path)
        pain = imported.max_pain()
        self.assertGreaterEqual(pain, 24800.0)
        self.assertLessEqual(pain, 25300.0)

    def test_missing_strike_column_is_refused(self):
        bad = os.path.join(self.tmp, "bad.csv")
        with open(bad, "w", encoding="utf-8") as handle:
            handle.write("foo,bar\n1,2\n")
        with self.assertRaises(chain.ChainError):
            chain.load("NIFTY", spot=SPOT, expiry=_date(EXPIRY), config={},
                       now=_load.SESSION, source="csv", path=bad)


def _date(text):
    import datetime as dt
    return dt.date.fromisoformat(text)


if __name__ == "__main__":
    unittest.main()

"""End-to-end: the eleven tools, in order, offline, on the 9 November 2026 session.

This is the test that matters most. It runs the actual command-line tools the
skills call, in the order the workflow calls them, against fixtures cut from
real data, and checks the numbers and the refusals.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _load

ROOT = _load.ROOT
NOW = "2026-11-09 10:45"


def run(tool, *args, expect=None):
    """Run a skill tool with the pinned clock and offline fixtures."""
    env = dict(os.environ)
    env["INDKIT_FIXTURE_DIR"] = _load.FIXTURES
    env["INDKIT_MASTER_DIR"] = _load.MASTER
    command = [sys.executable, os.path.join(ROOT, tool), *[str(a) for a in args]]
    result = subprocess.run(command, capture_output=True, text=True, env=env, cwd=os.getcwd())
    if expect is not None and result.returncode != expect:
        raise AssertionError(
            f"{tool} {' '.join(args)} exited {result.returncode}, expected {expect}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.dir)
        shutil.copytree(_load.FIXTURES, os.path.join(self.dir, "fixtures")) if False else None
        os.makedirs("trader", exist_ok=True)
        # a config with the example limits
        with open(os.path.join(ROOT, "trader-budget", "config.example.json"), "r", encoding="utf-8") as handle:
            config = json.load(handle)
        config["paper"] = True
        with open("trader/config.json", "w", encoding="utf-8") as handle:
            json.dump(config, handle)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.dir, ignore_errors=True)

    def read(self, name):
        with open(os.path.join("trader", name), "r", encoding="utf-8") as handle:
            return json.load(handle)

    # ---------------------------------------------------------------- scanner
    def test_01_scanner_daily(self):
        result = run("trader-scanner/scan.py", "daily", "--now", "2026-11-09 08:30", "--json")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "daily")
        self.assertGreaterEqual(len(payload["index"]), 3)
        nifty = [row for row in payload["index"] if row["underlying"] == "NIFTY"][0]
        self.assertAlmostEqual(nifty["spot"], 25044.65, places=2)
        self.assertGreater(nifty["rsi14"], 60)
        setups = [hit["setup"] for hit in payload["hits"] if hit["underlying"] == "NIFTY"]
        self.assertIn("breakout_20d", setups)

    def test_02_scanner_orb(self):
        result = run("trader-scanner/scan.py", "orb", "--now", NOW, "--json", expect=0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "orb")
        self.assertTrue(any(hit["direction"] == "PE" for hit in payload["hits"]))

    # ------------------------------------------------------------------ entry
    def test_03_entry_trigger_and_contract(self):
        run("trader-entry/entry.py", "check", "NIFTY", "--now", NOW, "--chain", "model", "--write", expect=0)
        entry = self.read("entry.json")
        self.assertEqual(entry["status"], "triggered")
        self.assertEqual(entry["direction"], "PE")
        self.assertEqual(entry["contract"]["strike"], 24850.0)
        self.assertEqual(entry["contract"]["lot"], 65)
        self.assertEqual(entry["contract"]["expiry"], "2026-11-17")
        self.assertGreater(entry["contract"]["premium"], 100)

    def test_04_entry_waits_before_the_break(self):
        result = run("trader-entry/entry.py", "check", "NIFTY", "--now", "2026-11-09 10:30",
                     "--chain", "model", expect=1)
        self.assertIn("WAITING", result.stdout)

    # ------------------------------------------------------------------- exit
    def test_05_exit_plan_translates_to_premium(self):
        run("trader-entry/entry.py", "check", "NIFTY", "--now", NOW, "--chain", "model", "--write", expect=0)
        run("trader-exit/exit.py", "plan", "--write", expect=0)
        plan = self.read("plan.json")
        self.assertEqual(plan["stop_rule"], "or-mid")
        self.assertAlmostEqual(plan["stop_index"], 24990.20, places=2)
        self.assertLess(plan["stop_premium"], plan["entry_premium"])
        self.assertGreater(plan["target_premium"], plan["entry_premium"])
        self.assertEqual(plan["target_r"], 1.5)
        self.assertAlmostEqual(plan["risk_per_unit"],
                               plan["entry_premium"] - plan["stop_premium"], places=2)
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["flat_by"], "15:15")
        # the target solves back to an index level below the entry for a put
        self.assertLess(plan["target_index"], plan["entry_index"])

    def test_06_exit_refuses_a_stop_that_wipes_the_option(self):
        run("trader-entry/entry.py", "check", "NIFTY", "--now", NOW, "--chain", "model", "--write", expect=0)
        result = run("trader-exit/exit.py", "plan", "--stop", "index-points", "--points", 9000,
                     "--no-write", expect=1)
        self.assertIn("100% loss", result.stdout)

    # ------------------------------------------------------------- timemachine
    def test_07_timemachine_replays(self):
        run("trader-entry/entry.py", "check", "NIFTY", "--now", NOW, "--chain", "model", "--write", expect=0)
        run("trader-exit/exit.py", "plan", "--write", expect=0)
        run("trader-timemachine/timemachine.py", "run", "--replays", "400", "--write",
            "--now", NOW, expect=0)
        result = self.read("timemachine.json")
        mc = result["mc"]
        self.assertEqual(mc["replays"], 400)
        self.assertGreaterEqual(mc["sessions_in_pool"], 20)
        total = mc["target_first"] + mc["stop_first"] + mc["neither"]
        self.assertAlmostEqual(total, 100.0, delta=0.3)
        self.assertIn("history", result)
        self.assertGreater(result["history"]["fired"], 0)
        self.assertIn("target", result["verdict"].lower())

    # ----------------------------------------------------------------- budget
    def test_08_budget_sizes_one_lot(self):
        run("trader-entry/entry.py", "check", "NIFTY", "--now", NOW, "--chain", "model", "--write", expect=0)
        run("trader-exit/exit.py", "plan", "--write", expect=0)
        run("trader-budget/size.py", "--write", expect=0)
        orders = self.read("orders.json")
        entry = orders["orders"][0]
        self.assertEqual(entry["qty"] % 65, 0)
        self.assertEqual(entry["exchange"], "NFO")
        self.assertEqual(entry["product"], "NRML")
        self.assertEqual(len(orders["exits"]), 3)          # stop, target, flat-by
        self.assertEqual(orders["exits"][0]["order_type"], "sl-m")
        self.assertGreater(entry["risk"], 0)

    # ------------------------------------------------------------------- risk
    def test_09_risk_passes_a_good_order(self):
        run("trader-entry/entry.py", "check", "NIFTY", "--now", NOW, "--chain", "model", "--write", expect=0)
        run("trader-exit/exit.py", "plan", "--write", expect=0)
        run("trader-budget/size.py", "--write", expect=0)
        run("trader-risk/check.py", "--now", NOW, expect=0)
        record = self.read("state/risk.json")
        self.assertEqual(record["blocked"], 0)
        self.assertEqual(record["verdicts"][0]["verdict"], "PASS")

    def test_10_risk_blocks_outside_the_window(self):
        run("trader-entry/entry.py", "check", "NIFTY", "--now", NOW, "--chain", "model", "--write", expect=0)
        run("trader-exit/exit.py", "plan", "--write", expect=0)
        run("trader-budget/size.py", "--write", expect=0)
        result = run("trader-risk/check.py", "--now", "2026-11-09 14:55", expect=1)
        self.assertIn("BLOCK", result.stdout)
        self.assertIn("window", result.stdout)

    def test_11_risk_blocks_an_oversized_order(self):
        run("trader-entry/entry.py", "check", "NIFTY", "--now", NOW, "--chain", "model", "--write", expect=0)
        run("trader-exit/exit.py", "plan", "--write", expect=0)
        # force 4 lots, which breaks the max-loss rule
        run("trader-budget/size.py", "--lots", "4", "--write", "--json", expect=0)
        result = run("trader-risk/check.py", "--now", NOW, expect=1)
        self.assertIn("max loss per trade", result.stdout)

    # ----------------------------------------------------------------- broker
    def test_12_broker_three_locks(self):
        run("trader-entry/entry.py", "check", "NIFTY", "--now", NOW, "--chain", "model", "--write", expect=0)
        run("trader-exit/exit.py", "plan", "--write", expect=0)
        run("trader-budget/size.py", "--write", expect=0)
        run("trader-risk/check.py", "--now", NOW, expect=0)

        # lock 2: no --yes means no fill
        blocked = run("trader-broker/ticket.py", "place", expect=1)
        self.assertIn("say yes explicitly", blocked.stdout)

        # lock 1: an un-passed order is not ready (new orders file, same ticket)
        ready = run("trader-broker/ticket.py", "show", expect=0)
        self.assertIn("READY", ready.stdout)

        filled = run("trader-broker/ticket.py", "place", "--yes", expect=0)
        self.assertIn("PAPER FILLED", filled.stdout)
        state = self.read("state/broker.json")
        self.assertEqual(len(state["positions"]), 1)
        self.assertEqual(state["positions"][0]["qty"], 65)

        status = run("trader-broker/ticket.py", "status", expect=0)
        self.assertIn("R)", status.stdout)

    def test_13_broker_refuses_when_risk_did_not_pass(self):
        run("trader-entry/entry.py", "check", "NIFTY", "--now", NOW, "--chain", "model", "--write", expect=0)
        run("trader-exit/exit.py", "plan", "--write", expect=0)
        run("trader-budget/size.py", "--write", expect=0)
        # no risk run at all
        result = run("trader-broker/ticket.py", "place", "--yes", expect=1)
        self.assertIn("NOT READY", result.stdout)

    # ----------------------------------------------------------------- alerts
    def test_14_alerts_from_plan(self):
        run("trader-entry/entry.py", "check", "NIFTY", "--now", NOW, "--chain", "model", "--write", expect=0)
        run("trader-exit/exit.py", "plan", "--write", expect=0)
        run("trader-alerts/alerts.py", "add", "--from-plan", expect=0)
        listing = run("trader-alerts/alerts.py", "list", expect=0)
        self.assertEqual(listing.stdout.count("armed"), 4)
        # a stop alert the price cannot have hit yet -> nothing rings
        run("trader-alerts/alerts.py", "check", "--now", NOW, expect=1)

    def test_15_time_alert_rings_once(self):
        run("trader-alerts/alerts.py", "add", "--time", "09:30", "--note", "test", expect=0)
        first = run("trader-alerts/alerts.py", "check", "--now", NOW, expect=0)
        self.assertIn("RING", first.stdout)
        second = run("trader-alerts/alerts.py", "check", "--now", NOW, expect=1)
        self.assertNotIn("RING", second.stdout)          # it does not ring twice

    # ---------------------------------------------------------------- analyst
    def test_16_analyst_modelled_chain_is_labelled(self):
        result = run("trader-analyst/analyst.py", "chain", "--underlying", "NIFTY",
                     "--chain", "model", "--now", NOW, expect=0)
        self.assertIn("MODELLED", result.stdout)
        self.assertIn("one-sigma", result.stdout)
        self.assertIn("need a REAL chain", result.stdout)

    def test_17_analyst_calendar_reads_the_master(self):
        result = run("trader-analyst/analyst.py", "calendar", "--now", NOW, "--json", expect=0)
        payload = json.loads(result.stdout)
        nifty = [row for row in payload["underlyings"] if row["underlying"] == "NIFTY"][0]
        self.assertEqual(nifty["lot"], 65)
        self.assertTrue(any(item["series"] == "weekly" for item in nifty["expiries"]))
        sensex = [row for row in payload["underlyings"] if row["underlying"] == "SENSEX"][0]
        self.assertEqual(sensex["expiries"][0]["weekday"], "Thursday")

    # ---------------------------------------------------------------- journal
    def test_18_journal_records_the_round_trip(self):
        run("trader-entry/entry.py", "check", "NIFTY", "--now", NOW, "--chain", "model", "--write", expect=0)
        run("trader-exit/exit.py", "plan", "--write", expect=0)
        run("trader-budget/size.py", "--write", expect=0)
        run("trader-risk/check.py", "--now", NOW, expect=0)
        run("trader-broker/ticket.py", "place", "--yes", expect=0)
        run("trader-broker/ticket.py", "flatten", "--yes", expect=0)
        stats = run("trader-journal/journal.py", "stats", "--json", expect=0)
        summary = json.loads(stats.stdout)["summary"]
        self.assertEqual(summary["trades"], 1)
        self.assertLess(summary["total_pnl"], 0)          # charges only: same premium in and out
        self.assertIn("charges_paid", summary)

    # ------------------------------------------------------------------- news
    def test_19_news_events_list(self):
        result = run("trader-news/news.py", "--events", expect=0)
        self.assertIn("RBI", result.stdout)
        self.assertIn("expires", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()

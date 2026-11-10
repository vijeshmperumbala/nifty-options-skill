"""Dhan adapter and the live-order gate - all offline.

Every HTTP call goes through an injected transport, so nothing here needs a
token or a network. The payloads are cut from Dhan's documented v2 shapes with
this pack's 9 Nov 2026 session numbers in them (NIFTY 25,044.65, the 24850 PE at
Rs 246.10), which keeps the assertions readable next to the worked example in
the README.
"""

import datetime as dt
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout

import _load

from indkit import brokers, chain as chains, clock, store
from indkit.brokers import dhan

FIX = os.path.join(_load.FIXTURES, "dhan")


def fixture(name):
    with open(os.path.join(FIX, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


class Transport:
    """A stand-in for Dhan: routes (method, path) to a fixture, records calls."""

    def __init__(self, routes=None, statuses=None):
        self.routes = dict(routes or {})
        self.statuses = dict(statuses or {})
        self.calls = []

    def __call__(self, method, url, headers, body):
        path = url.split("?")[0]
        if path.startswith(dhan.BASE):
            path = path[len(dhan.BASE):]
        self.calls.append({"method": method, "path": path, "headers": dict(headers),
                           "body": json.loads(body) if body else None,
                           "query": url.split("?")[1] if "?" in url else None})
        name = self.routes.get((method, path))
        if name is None:
            raise AssertionError(f"unexpected {method} {path}")
        status = self.statuses.get((method, path), 200)
        return status, json.dumps(fixture(name)).encode()


def adapter(routes=None, statuses=None, **kwargs):
    transport = Transport(routes, statuses)
    client = dhan.Dhan(client_id="1000000001", access_token="test-token",
                       transport=transport, throttle=False, **kwargs)
    return client, transport


DEFAULT_ROUTES = {
    ("POST", "/optionchain"): "optionchain.json",
    ("POST", "/optionchain/expirylist"): "expirylist.json",
    ("GET", "/fundlimit"): "funds.json",
    ("POST", "/orders"): "order_place.json",
    ("GET", "/orders/1123091024730"): "order_traded.json",
    ("GET", "/orders/1123091024731"): "order_rejected.json",
    ("GET", "/orders"): "orders.json",
    ("GET", "/positions"): "positions.json",
}


class DhanBasics(unittest.TestCase):
    def test_index_security_ids(self):
        self.assertEqual(dhan.UNDERLYINGS["NIFTY"]["security_id"], 13)
        self.assertEqual(dhan.UNDERLYINGS["SENSEX"]["security_id"], 51)
        self.assertEqual(dhan.UNDERLYINGS["BANKNIFTY"]["security_id"], 25)
        self.assertEqual(dhan.UNDERLYINGS["SENSEX"]["fno"], "BSE_FNO")
        self.assertEqual(dhan.INDIA_VIX_ID, 21)

    def test_available_needs_both_credentials(self):
        saved = {k: os.environ.pop(k, None) for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN")}
        try:
            self.assertFalse(dhan.Dhan().available())
            self.assertFalse(brokers.available({}))
            os.environ["DHAN_CLIENT_ID"] = "1000000001"
            self.assertFalse(dhan.Dhan().available())
            os.environ["DHAN_ACCESS_TOKEN"] = "token"
            self.assertTrue(dhan.Dhan().available())
            self.assertTrue(brokers.data_available({}))
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_unknown_broker_name_is_loud(self):
        with self.assertRaises(ValueError):
            brokers.get_adapter("zerodha", {})

    def test_unknown_underlying(self):
        with self.assertRaises(dhan.DhanError) as ctx:
            dhan.Dhan.segment_for("NIFTYIT")
        self.assertIn("does not carry", str(ctx.exception))


class DhanData(unittest.TestCase):
    def test_expiries_are_dates(self):
        client, transport = adapter(DEFAULT_ROUTES)
        days = client.expiries("NIFTY")
        self.assertEqual(days[0], dt.date(2026, 11, 9))
        self.assertEqual(len(days), 4)
        self.assertEqual(transport.calls[0]["body"],
                         {"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I"})

    def test_expiry_list_is_cached(self):
        client, transport = adapter(DEFAULT_ROUTES)
        client.expiries("NIFTY")
        client.expiries("NIFTY")
        self.assertEqual(len(transport.calls), 1)

    def test_chain_becomes_a_real_chain(self):
        client, transport = adapter(DEFAULT_ROUTES)
        chain = client.chain("NIFTY", "2026-11-17")
        self.assertEqual(chain.source, "dhan")
        self.assertTrue(chain.real)
        self.assertAlmostEqual(chain.spot, 25044.65, places=2)
        self.assertEqual(len(chain.rows), 5)

        leg = chain.leg(24850.0, "PE")
        self.assertAlmostEqual(leg["ltp"], 246.10, places=2)
        self.assertAlmostEqual(leg["iv"], 0.1742, places=4)        # 17.42% as a decimal
        self.assertEqual(leg["security_id"], 63411)
        self.assertEqual(leg["oi_change"], 598740 - 571220)
        self.assertAlmostEqual(leg["bid"], 245.90, places=2)
        self.assertAlmostEqual(leg["ask"], 246.25, places=2)
        self.assertAlmostEqual(leg["greeks"]["delta"], -0.5579, places=4)

    def test_chain_request_body_uses_the_documented_keys(self):
        client, transport = adapter(DEFAULT_ROUTES)
        client.chain("SENSEX", "2026-11-19")
        body = transport.calls[0]["body"]
        self.assertEqual(body, {"UnderlyingScrip": 51, "UnderlyingSeg": "IDX_I", "Expiry": "2026-11-19"})
        self.assertEqual(transport.calls[0]["headers"]["client-id"], "1000000001")
        self.assertEqual(transport.calls[0]["headers"]["access-token"], "test-token")

    def test_chain_is_cached_for_three_seconds(self):
        client, transport = adapter(DEFAULT_ROUTES)
        client.chain("NIFTY", "2026-11-17")
        client.chain("NIFTY", "2026-11-17")
        self.assertEqual(len(transport.calls), 1, "a second chain call inside 3s must not hit the wire")

    def test_empty_underlying_price_is_an_error(self):
        client, _ = adapter({("POST", "/optionchain"): "empty_chain.json"})
        with self.assertRaises((chains.ChainError, dhan.DhanError)):
            client.chain("NIFTY", "2026-11-17")

    def test_available_cash_reads_the_misspelt_key(self):
        client, _ = adapter(DEFAULT_ROUTES)
        self.assertAlmostEqual(client.available_cash(), 285340.55, places=2)

    def test_book(self):
        client, _ = adapter(DEFAULT_ROUTES)
        self.assertEqual(len(client.orders()), 2)
        self.assertEqual(client.positions()[0]["netQty"], 65)


class DhanErrors(unittest.TestCase):
    def test_bad_token_maps_to_dh901_with_a_hint(self):
        client, transport = adapter({("POST", "/optionchain/expirylist"): "error_dh901.json"},
                                    statuses={("POST", "/optionchain/expirylist"): 401})
        with self.assertRaises(dhan.DhanError) as ctx:
            client.expiries("NIFTY", fresh=True)
        error = ctx.exception
        self.assertEqual(error.code, "DH-901")
        self.assertIn("DHAN_CLIENT_ID", error.hint)
        self.assertIn("expired", str(error))

    def test_input_error_maps_to_dh905(self):
        client, _ = adapter({("POST", "/orders"): "error_dh905.json"},
                            statuses={("POST", "/orders"): 400})
        with self.assertRaises(dhan.DhanError) as ctx:
            client.place({"transactionType": "BUY"})
        self.assertEqual(ctx.exception.code, "DH-905")
        self.assertIn("STATIC IP", ctx.exception.hint)

    def test_the_real_auth_envelope_is_understood(self):
        """Dhan's live 401 body is {"data": {"808": "..."}, "status": "failed"} - captured 9 Nov 2026."""
        client, _ = adapter({("POST", "/optionchain/expirylist"): "error_808.json"},
                            statuses={("POST", "/optionchain/expirylist"): 401})
        with self.assertRaises(dhan.DhanError) as ctx:
            client.expiries("NIFTY", fresh=True)
        error = ctx.exception
        self.assertEqual(error.code, "808")
        self.assertIn("Authentication Failed", str(error))
        self.assertIn("DHAN_ACCESS_TOKEN", error.hint)

    def test_missing_credentials_never_reach_the_network(self):
        saved = {k: os.environ.pop(k, None) for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN")}
        try:
            client, transport = adapter(DEFAULT_ROUTES)
            client.client_id = None
            client.access_token = None
            with self.assertRaises(dhan.DhanError) as ctx:
                client.chain("NIFTY", "2026-11-17")
            self.assertIn("DHAN_CLIENT_ID", str(ctx.exception))
            self.assertEqual(transport.calls, [])
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    def test_network_failure_is_wrapped(self):
        client, _ = adapter({})

        def dead(method, url, headers, body):
            raise ConnectionError("no route to host")

        client._transport = dead
        with self.assertRaises(Exception):
            client.expiries("NIFTY", fresh=True)


class DhanOrders(unittest.TestCase):
    def test_market_buy_payload(self):
        client, _ = adapter()
        payload = client.build_order(transaction="BUY", segment="NSE_FNO", security_id=63411,
                                     quantity=65, order_type="market", product="CNC",
                                     correlation="ntdd02fb484e16f8-e")
        self.assertEqual(payload["dhanClientId"], "1000000001")
        self.assertEqual(payload["transactionType"], "BUY")
        self.assertEqual(payload["exchangeSegment"], "NSE_FNO")
        self.assertEqual(payload["productType"], "CNC")
        self.assertEqual(payload["orderType"], "MARKET")
        self.assertEqual(payload["validity"], "DAY")
        self.assertEqual(payload["securityId"], "63411")
        self.assertEqual(payload["quantity"], 65)
        self.assertEqual(payload["price"], "")            # MARKET sends an empty price, as documented
        self.assertEqual(payload["triggerPrice"], "")
        self.assertFalse(payload["afterMarketOrder"])
        self.assertLessEqual(len(payload["correlationId"]), 30)

    def test_limit_needs_a_price_and_sl_needs_a_trigger(self):
        client, _ = adapter()
        with self.assertRaises(dhan.DhanError):
            client.build_order(transaction="SELL", segment="NSE_FNO", security_id=63411,
                               quantity=65, order_type="limit")
        with self.assertRaises(dhan.DhanError):
            client.build_order(transaction="SELL", segment="NSE_FNO", security_id=63411,
                               quantity=65, order_type="sl-m")
        stop = client.build_order(transaction="SELL", segment="NSE_FNO", security_id=63411,
                                  quantity=65, order_type="STOP_LOSS_MARKET", trigger=178.34)
        self.assertEqual(stop["orderType"], "STOP_LOSS_MARKET")
        self.assertAlmostEqual(stop["triggerPrice"], 178.34, places=2)
        target = client.build_order(transaction="SELL", segment="NSE_FNO", security_id=63411,
                                    quantity=65, order_type="LIMIT", price=347.69)
        self.assertEqual(target["orderType"], "LIMIT")
        self.assertAlmostEqual(target["price"], 347.69, places=2)

    def test_correlation_is_truncated_to_thirty(self):
        client, _ = adapter()
        payload = client.build_order(transaction="BUY", segment="NSE_FNO", security_id=63411,
                                     quantity=65, correlation="x" * 60)
        self.assertEqual(len(payload["correlationId"]), 30)

    def test_place_returns_the_order_id(self):
        client, transport = adapter(DEFAULT_ROUTES)
        placed = client.place(client.build_order(transaction="BUY", segment="NSE_FNO",
                                                 security_id=63411, quantity=65))
        self.assertEqual(placed["orderId"], "1123091024730")
        self.assertEqual(transport.calls[0]["path"], "/orders")
        self.assertEqual(transport.calls[0]["method"], "POST")

    def test_wait_for_fill_stops_on_traded(self):
        client, _ = adapter(DEFAULT_ROUTES)
        status = client.wait_for_fill("1123091024730", tries=3, pause=0)
        self.assertEqual(status["orderStatus"], "TRADED")
        self.assertEqual(status["averageTradedPrice"], 246.10)
        self.assertEqual(status["filledQty"], 65)

    def test_wait_for_fill_stops_on_rejection(self):
        client, _ = adapter(DEFAULT_ROUTES)
        status = client.wait_for_fill("1123091024731", tries=3, pause=0)
        self.assertEqual(status["orderStatus"], "REJECTED")
        self.assertIn("static IP", status["omsErrorDescription"])

    def test_killswitch_query(self):
        client, transport = adapter({("POST", "/killswitch"): "funds.json"})
        client.killswitch(True)
        self.assertEqual(transport.calls[0]["query"], "status=activate")


# ---------------------------------------------------------------- the ticket gate
def load_ticket():
    path = os.path.join(_load.ROOT, "trader-broker", "ticket.py")
    spec = importlib.util.spec_from_file_location("ticket_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LiveGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nt-live-")
        os.makedirs(os.path.join(self.tmp, "state"), exist_ok=True)
        self.config = store.load_config(path=None, trader_root=self.tmp)
        self.ticket = load_ticket()
        self.now = "2026-11-09 10:45"
        self.order = {
            "symbol": "NIFTY26N1724850PE", "underlying": "NIFTY", "side": "buy", "right": "PE",
            "strike": 24850, "expiry": "2026-11-17", "qty": 65, "lots": 1,
            "premium_entry": 246.08, "premium_stop": 178.34, "premium_target": 347.69,
            "stop_index": 24990.2, "target_index": 25115.0, "risk": 4402.1, "reward": 6602.85,
            "reward_risk": 1.5, "order_type": "market", "product": "CNC", "validity": "DAY",
            "exchange": "NFO", "premium_source": "chain",
        }
        self.item = {"order": self.order, "fingerprint": "dd02fb484e16f84e",
                     "verdict": "PASS", "reasons": [], "ready": True}
        store.write_json("orders.json", {"orders": [self.order]}, self.config)
        store.write_json("state/risk.json", {"day": "2026-11-09", "verdicts": [
            {"fingerprint": "dd02fb484e16f84e", "verdict": "PASS", "reasons": []}]}, self.config)

        # a fake broker: real Dhan object, fixture transport, no network
        self.broker, self.transport = adapter({
            ("POST", "/orders"): "order_place.json",
            ("GET", "/orders/1123091024730"): "order_traded.json",
            ("GET", "/fundlimit"): "funds.json",
        })
        self.quote = {"premium": 246.10, "security_id": 63411, "bid": 245.90, "ask": 246.25,
                      "iv": 0.1742, "chain": None, "spot": 25044.65, "real_chain": True}
        self.config["max_entry_slippage_pct"] = 2.0
        self.config["order_poll_seconds"] = 0.0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch(self):
        self.ticket.contract_quote = lambda order, config, now: dict(self.quote)
        self.ticket.adapter_for = lambda config: self.broker

    def test_live_mode_needs_three_switches(self):
        self.assertFalse(self.ticket.live_mode(self.config))          # paper default
        self.config["paper"] = False
        self.assertFalse(self.ticket.live_mode(self.config))          # no broker
        self.config["live_broker"] = "dhan"
        self.assertFalse(self.ticket.live_mode(self.config))          # third switch still off
        self.config["live_trading_enabled"] = True
        self.assertTrue(self.ticket.live_mode(self.config))

    def test_preflight_refuses_when_live_trading_is_off(self):
        self._patch()
        report = self.ticket.preflight(self.item, self.config, self.now)
        self.assertTrue(report["blockers"])
        self.assertTrue(any("live_trading_enabled" in b for b in report["blockers"]))
        self.assertEqual(self.transport.calls, [])                    # nothing sent

    def test_preflight_blocks_a_slipped_premium(self):
        self._patch()
        self.config.update({"paper": False, "live_broker": "dhan", "live_trading_enabled": True})
        self.quote["premium"] = 246.08 * 1.05                          # 5% past the plan
        report = self.ticket.preflight(self.item, self.config, self.now)
        self.assertTrue(any("slipped" in b for b in report["blockers"]))
        self.assertAlmostEqual(report["slippage_pct"], 5.0, places=1)

    def test_preflight_passes_and_sends_nothing(self):
        self._patch()
        self.config.update({"paper": False, "live_broker": "dhan", "live_trading_enabled": True})
        report = self.ticket.preflight(self.item, self.config, self.now)
        with redirect_stdout(io.StringIO()) as out:
            self.ticket.render_preflight(report)
        self.assertEqual(report["blockers"], [])
        self.assertEqual(report["security_id"], 63411)
        self.assertIn("nothing has been sent", out.getvalue())
        writes = [c for c in self.transport.calls if c["method"] in ("POST", "PUT", "DELETE")]
        self.assertEqual(writes, [], "preflight must only read")

    def test_preflight_blocks_without_a_risk_verdict(self):
        self._patch()
        self.config.update({"paper": False, "live_broker": "dhan", "live_trading_enabled": True})
        item = dict(self.item, ready=False, verdict="NO VERDICT")
        report = self.ticket.preflight(item, self.config, self.now)
        self.assertTrue(any("no risk PASS" in b for b in report["blockers"]))

    def test_place_live_buys_then_attaches_both_exits(self):
        self._patch()
        self.config.update({"paper": False, "live_broker": "dhan", "live_trading_enabled": True})
        report = self.ticket.preflight(self.item, self.config, self.now)
        with redirect_stdout(io.StringIO()):
            position, code = self.ticket.place_live(self.item, self.broker, report,
                                                    self.config, self.now)
        self.assertEqual(code, 0)
        self.assertEqual(position["qty"], 65)
        self.assertEqual(position["entry_premium"], 246.10)           # the real fill, not the plan
        self.assertTrue(position["live"])
        self.assertEqual(position["order_ids"]["entry"], "1123091024730")

        bodies = [call["body"] for call in self.transport.calls if call["path"] == "/orders"]
        self.assertEqual(bodies[0]["transactionType"], "BUY")
        self.assertEqual(bodies[0]["orderType"], "MARKET")
        self.assertEqual(bodies[1]["transactionType"], "SELL")
        self.assertEqual(bodies[1]["orderType"], "STOP_LOSS_MARKET")
        self.assertAlmostEqual(bodies[1]["triggerPrice"], 178.34, places=2)
        self.assertAlmostEqual(bodies[2]["price"], 347.69, places=2)

        state = store.read_json("state/broker.json", self.config)
        self.assertEqual(len(state["positions"]), 1)
        journal = store.read_jsonl("journal.jsonl", self.config)
        self.assertEqual(journal[-1]["event"], "entry")
        self.assertFalse(journal[-1]["paper"])
        self.assertEqual(journal[-1]["entry_premium"], 246.10)

    def test_place_live_records_nothing_when_rejected(self):
        self._patch()
        self.config.update({"paper": False, "live_broker": "dhan", "live_trading_enabled": True})
        self.broker._transport = Transport({
            ("POST", "/orders"): "order_place.json",
            ("GET", "/orders/1123091024730"): "order_rejected.json",
            ("GET", "/fundlimit"): "funds.json",
        })
        report = self.ticket.preflight(self.item, self.config, self.now)
        with redirect_stdout(io.StringIO()) as out:
            position, code = self.ticket.place_live(self.item, self.broker, report,
                                                    self.config, self.now)
        self.assertIsNone(position)
        self.assertEqual(code, 1)
        self.assertIn("REJECTED", out.getvalue())
        state = store.read_json("state/broker.json", self.config, default={})
        self.assertEqual(state.get("positions", []), [])

    def test_flatten_live_sells_at_market_after_cancelling_exits(self):
        self._patch()
        self.config.update({"paper": False, "live_broker": "dhan", "live_trading_enabled": True})
        position = {
            "symbol": "NIFTY26N1724850PE", "underlying": "NIFTY", "qty": 65,
            "entry_premium": 246.10, "stop_premium": 178.34, "target_premium": 347.69,
            "risk": 4402.1, "charges_entry": 40.0, "expiry": "2026-11-17", "strike": 24850,
            "right": "PE", "live": True, "broker": "dhan", "security_id": 63411,
            "segment": "NSE_FNO", "fingerprint": "dd02fb484e16f84e",
            "order_ids": {"entry": "1123091024730", "stop": "1123091024732", "target": None},
        }
        store.write_json("state/broker.json", {"positions": [position]}, self.config)
        self.broker._transport = Transport({
            ("GET", "/orders/1123091024732"): "orders.json",
            ("DELETE", "/orders/1123091024732"): "order_place.json",
            ("POST", "/orders"): "order_place.json",
            ("GET", "/orders/1123091024730"): "order_traded.json",
        })
        with redirect_stdout(io.StringIO()):
            result = self.ticket.flatten_live(position, self.broker, self.config, self.now)
        self.assertIsNotNone(result)
        self.assertEqual(result["exit_premium"], 246.10)
        self.assertLess(result["pnl"], -40.0)          # flat premium: only the charges are lost
        sells = [call for call in self.broker._transport.calls
                 if call["path"] == "/orders" and call["method"] == "POST"]
        self.assertEqual(sells[0]["body"]["transactionType"], "SELL")
        self.assertEqual(sells[0]["body"]["orderType"], "MARKET")
        journal = store.read_jsonl("journal.jsonl", self.config)
        self.assertEqual(journal[-1]["event"], "exit")
        self.assertFalse(journal[-1]["paper"])
        self.assertAlmostEqual(journal[-1]["pnl"], result["pnl"], places=2)


class DhanChainWiring(unittest.TestCase):
    def test_chain_load_accepts_the_dhan_source(self):
        with open(os.path.join(_load.ROOT, "lib", "indkit", "chain.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('"dhan"', source)
        self.assertIn("brokers.data_available", source)

    def test_security_id_helper(self):
        chain = chains.Chain("NIFTY", "NSE", dt.date(2026, 11, 17), 25044.65,
                             {24850.0: {"PE": {"ltp": 246.1, "security_id": 63411}}}, "dhan",
                             clock.now_ist("2026-11-09 10:45"))
        self.assertEqual(chain.security_id(24850.0, "PE"), 63411)
        self.assertTrue(chain.real)


if __name__ == "__main__":
    unittest.main()

"""The IST session clock, the holiday calendar and the entry windows."""

import datetime as dt
import unittest

import _load  # noqa: F401  (sets the path and offline env)
from indkit import clock


class TestGrid(unittest.TestCase):
    def test_five_minute_grid_has_75_bars(self):
        grid = clock.bar_grid("5m")
        self.assertEqual(len(grid), 75)
        self.assertEqual(grid[0], dt.time(9, 15))
        self.assertEqual(grid[-1], dt.time(15, 25))

    def test_one_minute_grid(self):
        grid = clock.bar_grid("1m")
        self.assertEqual(len(grid), 375)
        self.assertEqual(grid[0], dt.time(9, 15))
        self.assertEqual(grid[-1], dt.time(15, 29))

    def test_bar_completes_after_its_window(self):
        start = clock.now_ist("2026-11-09 10:40")
        self.assertFalse(clock.is_bar_complete(start, "5m", "2026-11-09 10:43"))
        self.assertTrue(clock.is_bar_complete(start, "5m", "2026-11-09 10:45"))

    def test_opening_range_ends_at_0930(self):
        self.assertEqual(clock.opening_range_end(15), dt.time(9, 30))
        self.assertEqual(clock.opening_range_end(30), dt.time(9, 45))


class TestSession(unittest.TestCase):
    def test_states_through_the_day(self):
        self.assertEqual(clock.market_state("2026-11-09 08:30"), "closed")
        self.assertEqual(clock.market_state("2026-11-09 09:05"), "preopen")
        self.assertEqual(clock.market_state("2026-11-09 09:15"), "open")
        self.assertEqual(clock.market_state("2026-11-09 15:30"), "open")
        self.assertEqual(clock.market_state("2026-11-09 15:31"), "post")

    def test_holiday_is_closed(self):
        # Diwali-Balipratipada: the exchange is shut on a Tuesday
        self.assertFalse(clock.is_trading_day(dt.date(2026, 11, 10)))
        self.assertEqual(clock.holiday_name(dt.date(2026, 11, 10)), "Diwali-Balipratipada")
        self.assertEqual(clock.market_state("2026-11-10 10:00"), "closed")

    def test_weekend_is_closed(self):
        self.assertFalse(clock.is_trading_day(dt.date(2026, 11, 7)))

    def test_next_and_previous_trading_day_skip_the_holiday(self):
        self.assertEqual(clock.next_trading_day(dt.date(2026, 11, 9)), dt.date(2026, 11, 11))
        self.assertEqual(clock.previous_trading_day(dt.date(2026, 11, 11)), dt.date(2026, 11, 9))

    def test_entry_window(self):
        config = {"no_entries_before": "09:30", "no_entries_after": "14:30", "flat_by": "15:15"}
        early = clock.phase("2026-11-09 09:20", config)
        self.assertFalse(early["entries_ok"])
        self.assertIn("09:30", early["reason"])

        middle = clock.phase("2026-11-09 10:45", config)
        self.assertTrue(middle["entries_ok"])
        self.assertFalse(middle["past_flat_by"])

        late = clock.phase("2026-11-09 14:45", config)
        self.assertFalse(late["entries_ok"])

        flat = clock.phase("2026-11-09 15:16", config)
        self.assertTrue(flat["past_flat_by"])

    def test_describe_session_says_why(self):
        self.assertIn("market closed", clock.describe_session("2026-11-07 10:00"))
        self.assertIn("open", clock.describe_session("2026-11-09 11:00"))

    def test_clock_parsing(self):
        self.assertEqual(clock.now_ist("2026-11-09 10:15").hour, 10)
        self.assertEqual(clock.now_ist(dt.date(2026, 11, 9)).time(), clock.SESSION_START)


if __name__ == "__main__":
    unittest.main()

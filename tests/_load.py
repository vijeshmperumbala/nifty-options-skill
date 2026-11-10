"""Shared test setup: put lib/ on the path and force offline fixtures.

The fixtures are cut from real data captured on 9 November 2026, so every
assertion in this suite is a reproducible fact about that session rather than a
number that happened to come out of the code.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
MASTER = os.path.join(FIXTURES, "master")

for path in (os.path.join(ROOT, "lib"), ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

# no request leaves the machine during tests
os.environ["INDKIT_FIXTURE_DIR"] = FIXTURES
os.environ["INDKIT_MASTER_DIR"] = MASTER

SESSION = "2026-11-09 10:45"        # the pinned clock for the worked example
DAY = "2026-11-09"

"""indkit - the small standard-library toolkit behind the NIFTY/SENSEX options skills.

Everything here is plain Python 3.8+ and reads either free public data (Yahoo
index bars, Kite's public instrument dumps, Google News RSS) or files you give
it. No API keys, no pip installs, no orders.

Modules
    clock        IST session clock, NSE holidays, bar grid, entry windows
    instruments  contract master: lots, expiries, strikes, broker symbols
    feed         index OHLC from Yahoo, normalised to IST and to completed bars
    bs           Black-Scholes price/greeks and the implied-vol solver
    chain        option chain: live adapters, CSV import, and a modelled fallback
    costs        the Indian charge stack (STT, exchange, GST, stamp, SEBI)
    store        the trader/ folder: read/write JSON state
    rules        SEBI/NSE market-structure constants the risk gate enforces
"""

__version__ = "1.0.0"

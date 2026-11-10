"""Broker adapters. One module per broker, same shape, no shared state.

Only Dhan is implemented. Adding another broker (Kite Connect, Upstox, Angel
One) means writing the same handful of methods and registering it in
`get_adapter`, so `trader-broker` does not have to know whose API it is talking
to.
"""

from __future__ import annotations

from . import dhan

ADAPTERS = {"dhan": dhan.Dhan}


def get_adapter(name=None, config=None):
    """The configured adapter, or None when no broker is wired up."""
    name = (name or (config or {}).get("live_broker") or "").strip().lower()
    if not name:
        return None
    adapter = ADAPTERS.get(name)
    if adapter is None:
        raise ValueError(f"unknown broker '{name}' (known: {', '.join(sorted(ADAPTERS))})")
    return adapter.from_env(config)


def available(config=None):
    """True when an adapter is configured AND its credentials are in the environment."""
    try:
        adapter = get_adapter(config=config)
    except Exception:                                              # noqa: BLE001
        return False
    return bool(adapter and adapter.available())


def data_available(config=None):
    """True when any adapter can supply market data with the current environment."""
    for adapter in ADAPTERS.values():
        try:
            if adapter().available():
                return True
        except Exception:                                          # noqa: BLE001
            continue
    return False


__all__ = ["dhan", "get_adapter", "available", "data_available", "ADAPTERS"]

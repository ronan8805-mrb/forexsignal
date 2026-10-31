"""Minimal stand-in for the MetaTrader5 package, driven by historical daily bars.
Used only for tests: the engine runs at each day's open, then the day 'plays out'
(server-side stops are checked against that day's low), then the clock advances."""
from __future__ import annotations

from types import SimpleNamespace as NS

ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_REAL = 0, 2
SYMBOL_TRADE_MODE_DISABLED, SYMBOL_TRADE_MODE_FULL = 0, 4
TIMEFRAME_D1 = 16408
ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
TRADE_ACTION_DEAL = 1
ORDER_TIME_GTC = 0
ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2


class FakeMT5:
    def __init__(self, data: dict[str, list[dict]], spread: float = 0.0, demo: bool = True, equity: float = 100_000.0,
                 margin_rate: float = 0.0):
        self.data = data                      # broker symbol -> bars [{time(s), open, high, low, close, tick_volume}]
        self.k = {s: 0 for s in data}         # index of the current (forming) bar per symbol
        self.spread, self.demo, self.balance, self.margin_rate = spread, demo, equity, margin_rate
        self.disabled: set[str] = set()       # symbols listed but not tradable (quotes only)
        self.positions: dict[int, NS] = {}
        self.deals: list[NS] = []
        self.next_ticket = 1000
        self.sent: list[dict] = []
        self.entries: list[int] = []          # bar time (s) of every fill
        self.exits: list[tuple] = []          # (bar time, price, reason) of every close
        # module-style constants so the engine can use fake.ORDER_TYPE_BUY etc.
        for k, v in globals().items():
            if k.isupper():
                setattr(self, k, v)

    # ---- clock ---------------------------------------------------------------
    def set_index(self, sym: str, k: int) -> None:
        self.k[sym] = k

    def play_bar(self, sym: str) -> None:
        """Bar k trades: stops are hit if low <= sl (fill at min(sl, open))."""
        b = self.data[sym][self.k[sym]]
        for t, p in list(self.positions.items()):
            if p.symbol == sym and p.sl and b["low"] <= p.sl:
                self._close(p, min(p.sl, b["open"]), b["time"])

    # ---- API surface used by engine.py ---------------------------------------
    def initialize(self, **kw): return True
    def shutdown(self): return True
    def last_error(self): return (0, "ok")

    def account_info(self):
        used = sum(self.order_calc_margin(0, p.symbol, p.volume, p.price_open) for p in self.positions.values())
        return NS(login=1, server="fake", equity=self.balance, balance=self.balance, currency="USD",
                  margin_free=self.balance - used,
                  trade_mode=ACCOUNT_TRADE_MODE_DEMO if self.demo else ACCOUNT_TRADE_MODE_REAL)

    def symbols_get(self): return [NS(name=s) for s in self.data]

    def symbol_info(self, s):
        if s not in self.data:
            return None
        return NS(name=s, digits=4, point=0.0001, trade_stops_level=0, volume_step=0.01, volume_min=0.01,
                  volume_max=1e6, filling_mode=1,
                  trade_mode=SYMBOL_TRADE_MODE_DISABLED if s in self.disabled else SYMBOL_TRADE_MODE_FULL)

    def symbol_select(self, s, on=True): return True

    def symbol_info_tick(self, s):
        b = self.data[s][self.k[s]]
        return NS(bid=b["open"], ask=b["open"] + self.spread, time=b["time"])

    def copy_rates_from_pos(self, s, tf, start, n):
        k = self.k[s]
        hi = k - start + 1                     # start=1 -> bars before the forming bar
        lo = max(0, hi - n)
        return [dict(b, tick_volume=b.get("tick_volume", 1000)) for b in self.data[s][lo:hi]]

    def order_calc_margin(self, typ, s, vol, price):
        return vol * price * self.margin_rate          # margin_rate 1.0 = exchange stock (full value), 0.01 = 1:100

    def order_calc_profit(self, typ, s, vol, p_open, p_close):
        return (p_close - p_open) * vol * (1 if typ == ORDER_TYPE_BUY else -1)

    def positions_get(self, **kw): return list(self.positions.values())

    def history_deals_get(self, position=None, **kw):
        return [d for d in self.deals if d.position_id == position]

    def order_send(self, req):
        self.sent.append(req)
        s, tick = req["symbol"], self.symbol_info_tick(req["symbol"])
        if req["type"] == ORDER_TYPE_BUY and "position" not in req:
            t = self.next_ticket; self.next_ticket += 1
            p = NS(ticket=t, identifier=t, symbol=s, magic=req["magic"], volume=req["volume"],
                   price_open=tick.ask, time=tick.time, sl=req["sl"])
            self.positions[t] = p
            self.entries.append(tick.time)
            self.deals.append(NS(position_id=t, time=tick.time, price=tick.ask, profit=0.0, commission=0.0, swap=0.0))
            return NS(retcode=10009, order=t, price=tick.ask, comment="done")
        p = self.positions.get(req["position"])
        if p is None:
            return NS(retcode=10013, order=0, price=0, comment="invalid")
        self._close(p, tick.bid, tick.time)
        return NS(retcode=10009, order=p.ticket, price=tick.bid, comment="done")

    def _close(self, p, px, t):
        pnl = (px - p.price_open) * p.volume
        self.exits.append((t, px, (px - p.price_open) / (p.price_open - p.sl) if p.price_open > p.sl else 0.0))
        self.balance += pnl
        self.deals.append(NS(position_id=p.ticket, time=t, price=px, profit=pnl, commission=0.0, swap=0.0))
        del self.positions[p.ticket]

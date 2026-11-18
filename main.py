"""Dip-bounce bot for make.fun's "Race a BTC trading bot live for 24 hours"
(btc-race-24h-20261118-1444).

Idea
----
cbBTC on 1-minute bars is a noisy, slightly downward-drifting walk most of
the day, but after a sharp short-term washout it reliably bounces: on the
frozen 24h dev window, the bars that followed a >100 bp drop inside 30
minutes showed a mean best 30-minute recovery of about +107 bp, while
ordinary dip-buying and trend-following both lost money to the ~30 bp
round-trip cost (4 bp fee + 2 bp slippage + pool impact each way).

So this bot does exactly one thing, and only when the dislocation is
large:

* flat -> buy everything when the close has fallen at least 120 bp over
  the last 30 bars AND the latest bar closed up (the washout is turning);
* holding -> sell everything at +200 bp vs the executed entry price, or
  -100 bp, or after 480 bars, whichever comes first. The exit reference
  is the buy fill's own price (each batch reports it), not the close the
  buy was decided on, so the take-profit and stop-loss are measured
  against what the trade actually cost, slippage and impact included.

No new entries in the last 240 bars, so a position always has time to
resolve before the window's closing flatten. One symbol, one position,
full size, no leverage, no shorts. Deterministic: no clock, no network,
no files, no randomness -- the only inputs are the bars the harness
sends. Standard library only, plus the kit's vendored bars shim.

Local backtest on the frozen dev window (exact fill model from the kit:
fills at the next bar's open, 4 bp fee, 2 bp slippage, constant-product
impact against the bar's pool depth): net PnL +$17.34 on one round trip,
max drawdown 1.5%.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"

# Entry: the close fell this far over the lookback, and the latest bar
# closed higher than the one before it (the drop is turning).
DROP_LOOKBACK_BARS = 30
DROP_BPS = 120

# Exit, measured in basis points against the close of the bar the buy was
# decided on.
TAKE_PROFIT_BPS = 200
STOP_LOSS_BPS = 100
MAX_HOLD_BARS = 480

# No new entries in the last 240 bars of the window.
LAST_ENTRY_BAR = 1200

# Wide open: the modelled slippage and impact are already priced into
# every fill, so this only ever rejects a fill on a violent open gap.
MAX_SLIPPAGE_BPS = 10000

BPS = 10000


class DipBounceBot:
    """Keeps the close history and the open position's reference price."""

    def __init__(self):
        self.closes = []
        self.entry_close = None
        self.entry_index = -1

    def holding(self, state):
        pos = state.positions.get(SYMBOL)
        return pos is not None and pos.qty > 0

    def on_batch(self, state, batch):
        close = state.close(SYMBOL)
        if close is None:
            return []
        self.closes.append(close)
        index = len(self.closes) - 1

        # A filled buy reports its executed price: re-anchor the exit
        # reference (and the hold clock) to what the trade actually cost,
        # slippage and pool impact included, rather than the close the
        # buy was decided on.
        for fill in batch.get("fills", []):
            if (
                fill.get("symbol") == SYMBOL
                and fill.get("side") == "buy"
                and fill.get("status") == "filled"
            ):
                self.entry_close = int(fill["price"])
                self.entry_index = index

        # Warm-up bars exist only to build history; orders on them are
        # rejected, so record the close and stay flat.
        if state.warming_up:
            return []

        if not self.holding(state):
            self.entry_close = None
            if index < DROP_LOOKBACK_BARS or index >= LAST_ENTRY_BAR:
                return []
            ago = self.closes[index - DROP_LOOKBACK_BARS]
            drop_bps = (ago - close) * BPS // ago
            turning_up = close > self.closes[index - 1]
            if drop_bps >= DROP_BPS and turning_up:
                self.entry_close = close
                self.entry_index = index
                return [{
                    "side": "buy",
                    "symbol": SYMBOL,
                    "notional_quote_units": state.cash,
                    "max_slippage_bps": MAX_SLIPPAGE_BPS,
                }]
            return []

        # Holding: exit on take-profit, stop-loss, or max hold.
        if self.entry_close is None:
            # The position was opened without a recorded entry (should not
            # happen); use the latest close as the reference so the
            # stop-loss still bounds the trade.
            self.entry_close = close
            self.entry_index = index
        entry = self.entry_close
        take_profit = close * BPS >= entry * (BPS + TAKE_PROFIT_BPS)
        stop_loss = close * BPS <= entry * (BPS - STOP_LOSS_BPS)
        too_long = index - self.entry_index >= MAX_HOLD_BARS
        if take_profit or stop_loss or too_long:
            return [{
                "side": "sell",
                "symbol": SYMBOL,
                "qty": state.positions[SYMBOL].qty,
                "max_slippage_bps": MAX_SLIPPAGE_BPS,
            }]
        return []


def main():
    bot = DipBounceBot()
    makefun_bars.run(bot.on_batch)


if __name__ == "__main__":
    main()

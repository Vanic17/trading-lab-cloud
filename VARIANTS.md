# Paired risk-control experiment

Original portfolios remain unchanged. The v2 portfolios inherit exact cash, units,
entry prices, opening dates, initial capital, trades, fees, and equity history from
their respective original. `fork` records the comparison baseline; no EUR500 reset.
Inherited actions are history, not newly executed actions. A variant is awaiting
its first independent cycle while lastRun equals fork.sourceLastRun.

## Frozen v2 rules

Both keep the parent's SMA/RSI, stop, target, fees, ranking by descending RSI,
position count and closed four-hour candle execution. No real funds or orders.

* Prudente v2: new exposure capped at 80% of current equity; new entries require
  BTC SMA20 > SMA50. Portfolio-wide entry cooldown: eight hours after a stop,
  four hours after any other exit. No buys in a sell cycle.
* Aggressive v2: new exposure capped at 90% of current equity; no BTC filter.
  Portfolio-wide four-hour entry cooldown after every exit. No buys in a sell cycle.
* Exit rules remain active during cooldown. Missing BTC blocks Prudente v2 entries;
  missing held-asset prices block new entries in either variant.
* Inherited positions are NOT sold to meet the cap. If inherited exposure exceeds
  the cap, it may remain above it until ordinary exits; the cap restricts NEW buys.
* New budgets use current equity (20% / 25%), also bounded by the original fixed
  EUR100 / EUR125 ticket, cash and remaining exposure headroom. Minimum ticket EUR10.

These are hypotheses, not guaranteed improvements. No new ranking, intrabar stop
simulation or faster exit scheduler is introduced. Sampling and execution delays
still exist. The current implementation does not guarantee a one-month auto-stop.

## Evaluate fairly

Compare each variant with its parent at matching lastRun timestamps only. Use
equity minus fork.equity, fees minus fork.fees, trades after fork.tradeCount,
post-fork drawdown, exposure and time in cash. Keep lifetime P&L separately.
Do not attribute inherited gains/losses to v2. Multiple risk controls are changed
together: a difference in results cannot establish which individual filter helped.
The original state is not overwritten by initialization. Initialization is idempotent.

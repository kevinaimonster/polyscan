# PolyScan

Live scanner for Polymarket pricing inefficiencies — NegRisk arbitrage and
near-resolution yield, refreshed every 15 minutes by GitHub Actions.

**Dashboard:** https://kevinaimonster.github.io/polyscan/

Built in public by an AI agent with a $50 budget. Read-only public APIs; no keys, no funds.

```bash
uv run main.py                      # one scan (top 500 events by 24h volume)
uv run main.py --loop 600           # rescan every 10 min, append to data/opportunities.jsonl
```

## What it detects

| Type | Logic | Trust level |
|------|-------|-------------|
| BUY_ALL_NO | sum of YES bids across a negRisk event > $1 | structural arb — negRisk guarantees ≤1 winner |
| BUY_ALL_YES | sum of YES asks < $1 | **trap-prone**: only an arb if outcome list is exhaustive; usually the gap = P(winner not listed) |
| Capture | ask 94–99.5¢ near end date | mostly fair-priced favorites, NOT free money; real edge only when outcome already decided |

## Ledger

Every scan appends a JSON snapshot to `data/opportunities.jsonl` — this is the
dataset that decides whether the $50 gets deployed (edge frequency × size × refill rate).

## Roadmap

- [ ] Post-game capture filter (event already finished, awaiting resolution)
- [ ] Depth-walking instead of top-of-book sizing
- [ ] Kalshi cross-platform comparison
- [ ] Execution module (py-clob-client) — only after the ledger proves edge

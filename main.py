"""Polymarket opportunity scanner — Phase 0 (paper only, $0 at risk).

Detects:
  1. NegRisk multi-outcome arbitrage:
     - BUY-ALL-NO: sum of YES bids across outcomes > $1  -> guaranteed profit
       (safe under "at most one outcome wins", which negRisk enforces)
     - BUY-ALL-YES: sum of YES asks < $1 -> profit ONLY if outcomes are
       exhaustive (flagged, must verify by hand)
  2. Near-resolution capture: asks in [0.94, 0.995] close to end date,
     ranked by annualized yield.

Usage:
  uv run main.py [--max-events N] [--loop SECONDS]
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
BANKROLL = 50.0
MIN_EDGE = 0.005          # ignore "arbs" thinner than 0.5c per $1
CAPTURE_ASK_LO = 0.94
CAPTURE_ASK_HI = 0.995
CAPTURE_MAX_DAYS = 45
DATA_DIR = Path(__file__).parent / "data"
DOCS_DIR = Path(__file__).parent / "docs"

HEADERS = {"User-Agent": "polyscan/0.1 (paper-trading research)"}


def f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def jloads(s, default=None):
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def fetch_events(client, max_events):
    """Active events, highest 24h volume first, then long tail."""
    events, offset = [], 0
    while len(events) < max_events:
        params = {"closed": "false", "limit": 100, "offset": offset,
                  "order": "volume24hr", "ascending": "false"}
        r = client.get(f"{GAMMA}/events", params=params)
        if r.status_code != 200:  # retry without sort if gamma rejects it
            r = client.get(f"{GAMMA}/events", params={
                "closed": "false", "limit": 100, "offset": offset})
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        events.extend(batch)
        offset += 100
        time.sleep(0.15)
    return events[:max_events]


def open_markets(event):
    for m in event.get("markets") or []:
        if m.get("closed") or not m.get("active"):
            continue
        if m.get("enableOrderBook") is False or m.get("acceptingOrders") is False:
            continue
        tokens = jloads(m.get("clobTokenIds"), [])
        if not tokens:
            continue
        m["_yes_token"] = tokens[0]
        yield m


def fetch_books(client, token_ids):
    books = {}
    ids = list(dict.fromkeys(token_ids))
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        r = client.post(f"{CLOB}/books", json=[{"token_id": t} for t in chunk])
        if r.status_code != 200:
            print(f"  ! /books chunk failed: HTTP {r.status_code}", file=sys.stderr)
            continue
        for b in r.json():
            books[b.get("asset_id")] = b
        time.sleep(0.25)
    return books


def best_levels(book):
    """-> (bid_price, bid_size, ask_price, ask_size), None entries if empty."""
    if not book:
        return None, None, None, None
    bids = [(f(l["price"]), f(l["size"])) for l in book.get("bids") or []]
    asks = [(f(l["price"]), f(l["size"])) for l in book.get("asks") or []]
    bid = max(bids, key=lambda x: x[0], default=(None, None))
    ask = min(asks, key=lambda x: x[0], default=(None, None))
    return bid[0], bid[1], ask[0], ask[1]


def days_until(iso):
    if not iso:
        return None
    try:
        end = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (end - datetime.now(timezone.utc)).total_seconds() / 86400


def scan_negrisk(event, books):
    """Check sum-of-YES-prices consistency across a negRisk event."""
    legs = []
    for m in open_markets(event):
        bid, bid_sz, ask, ask_sz = best_levels(books.get(m["_yes_token"]))
        legs.append({"question": m.get("question"), "bid": bid, "bid_size": bid_sz,
                     "ask": ask, "ask_size": ask_sz})
    if len(legs) < 2:
        return []

    found = []
    base = {"event": event.get("title"), "slug": event.get("slug"),
            "n_outcomes": len(legs), "end": event.get("endDate")}

    # BUY-ALL-NO: sell $1 of YES across the board via NO purchases.
    if all(l["bid"] is not None for l in legs):
        sum_bids = sum(l["bid"] for l in legs)
        edge = sum_bids - 1.0
        if edge > MIN_EDGE:
            shares = min(l["bid_size"] for l in legs)
            cost_per_set = len(legs) - sum_bids  # cost of one full NO set
            shares = min(shares, BANKROLL / max(cost_per_set, 0.01))
            found.append({**base, "type": "BUY_ALL_NO", "edge_per_set": round(edge, 4),
                          "sets": round(shares, 1),
                          "max_profit": round(edge * shares, 2),
                          "capital_needed": round(cost_per_set * shares, 2),
                          "legs": legs})

    # BUY-ALL-YES: only a true arb if outcome list is exhaustive.
    if all(l["ask"] is not None for l in legs):
        sum_asks = sum(l["ask"] for l in legs)
        edge = 1.0 - sum_asks
        if edge > MIN_EDGE:
            shares = min(l["ask_size"] for l in legs)
            shares = min(shares, BANKROLL / max(sum_asks, 0.01))
            found.append({**base, "type": "BUY_ALL_YES (verify exhaustive!)",
                          "edge_per_set": round(edge, 4), "sets": round(shares, 1),
                          "max_profit": round(edge * shares, 2),
                          "capital_needed": round(sum_asks * shares, 2),
                          "legs": legs})
    return found


def scan_captures(events, client):
    """Markets trading 94-99.5c near their end date -> annualized yield list."""
    candidates = []
    for ev in events:
        for m in open_markets(ev):
            ask = f(m.get("bestAsk"))
            days = days_until(m.get("endDate"))
            if ask is None or days is None:
                continue
            if CAPTURE_ASK_LO <= ask <= CAPTURE_ASK_HI and 0 < days <= CAPTURE_MAX_DAYS:
                candidates.append((m, ev, days))
    books = fetch_books(client, [m["_yes_token"] for m, _, _ in candidates])
    out = []
    for m, ev, days in candidates:
        _, _, ask, ask_sz = best_levels(books.get(m["_yes_token"]))
        if ask is None or not (CAPTURE_ASK_LO <= ask <= CAPTURE_ASK_HI):
            continue
        yld = (1.0 - ask) / ask
        out.append({"question": m.get("question"), "event": ev.get("title"),
                    "ask": ask, "ask_size": ask_sz, "days_left": round(days, 1),
                    "yield": round(yld * 100, 2),
                    "apy": round(yld / max(days, 0.5) * 365 * 100, 1),
                    "profit_on_50": round(min(BANKROLL / ask, ask_sz or 0) * (1 - ask), 2)})
    out.sort(key=lambda x: -x["apy"])
    return out


def run_scan(max_events):
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with httpx.Client(headers=HEADERS, timeout=30) as client:
        events = fetch_events(client, max_events)
        print(f"[{ts}] fetched {len(events)} active events")

        negrisk_events = [e for e in events if e.get("negRisk")]
        tokens = [m["_yes_token"] for e in negrisk_events for m in open_markets(e)]
        print(f"  negRisk events: {len(negrisk_events)} ({len(tokens)} order books)")
        books = fetch_books(client, tokens)

        arbs = []
        for e in negrisk_events:
            arbs.extend(scan_negrisk(e, books))
        arbs.sort(key=lambda a: -a["max_profit"])

        captures = scan_captures(events, client)

    print(f"\n=== NegRisk arbitrage: {len(arbs)} found ===")
    for a in arbs[:15]:
        print(f"  [{a['type']}] {a['event']}")
        print(f"    edge {a['edge_per_set']*100:.2f}c/set x {a['sets']} sets"
              f" -> max profit ${a['max_profit']} on ${a['capital_needed']}"
              f" ({a['n_outcomes']} legs, ends {a['end']})")

    print(f"\n=== Near-resolution captures (top 10 by APY) ===")
    for c in captures[:10]:
        print(f"  {c['ask']*100:.1f}c, {c['days_left']}d left, {c['yield']}% "
              f"(~{c['apy']}% APY), ${c['profit_on_50']} profit on $50 | {c['question']}")

    DATA_DIR.mkdir(exist_ok=True)
    snapshot = {"ts": ts, "events_scanned": len(events),
                "negrisk_arbs": arbs, "captures": captures[:50]}
    with open(DATA_DIR / "opportunities.jsonl", "a") as fh:
        fh.write(json.dumps(snapshot) + "\n")

    DOCS_DIR.mkdir(exist_ok=True)
    scan_count = sum(1 for _ in open(DATA_DIR / "opportunities.jsonl"))
    with open(DOCS_DIR / "data.json", "w") as fh:
        json.dump({"generated": ts, "events_scanned": len(events),
                   "scan_count": scan_count,
                   "arbs": arbs[:20], "captures": captures[:30]}, fh)
    print(f"\nsnapshot appended to data/opportunities.jsonl; docs/data.json updated")
    return snapshot


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--max-events", type=int, default=500)
    p.add_argument("--loop", type=int, default=0, help="rescan every N seconds")
    args = p.parse_args()
    while True:
        try:
            run_scan(args.max_events)
        except httpx.HTTPError as e:
            print(f"scan failed: {e}", file=sys.stderr)
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()

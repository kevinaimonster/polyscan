"""Push newly-detected arbs to a Telegram channel.

Reads docs/data.json (written by main.py), dedupes against data/alerted.json,
posts anything new via the Telegram bot API. No-op unless TELEGRAM_BOT_TOKEN
and TELEGRAM_CHAT_ID are set, so it is safe to run unconditionally in CI.
"""

import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).parent
STATE = ROOT / "data" / "alerted.json"
MIN_PROFIT = float(os.environ.get("ALERT_MIN_PROFIT", "0.5"))


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("telegram not configured; skipping alerts")
        return

    snap = json.loads((ROOT / "docs" / "data.json").read_text())
    seen = json.loads(STATE.read_text()) if STATE.exists() else {}
    fresh = []
    for a in snap.get("arbs", []):
        key = f"{a['slug']}|{a['type']}"
        prev = seen.get(key, 0.0)
        # alert on first sight, or if the edge widened by 50%+
        if a["max_profit"] >= MIN_PROFIT and a["edge_per_set"] > prev * 1.5:
            fresh.append(a)
            seen[key] = a["edge_per_set"]

    for a in fresh:
        safe = a["type"] == "BUY_ALL_NO"
        msg = (
            f"{'🟢 STRUCTURAL ARB' if safe else '🟡 SUM<$1 (verify exhaustive)'}\n"
            f"<b>{a['event']}</b>\n"
            f"edge {a['edge_per_set'] * 100:.2f}¢/set × {a['sets']} sets "
            f"→ max ${a['max_profit']} on ${a['capital_needed']}\n"
            f"{a['n_outcomes']} legs · ends {(a.get('end') or '')[:10]}\n"
            f"https://polymarket.com/event/{a['slug']}"
        )
        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20,
        )
        print(f"alert {a['slug']}: HTTP {r.status_code}")

    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(seen))
    print(f"{len(fresh)} alert(s) sent")


if __name__ == "__main__":
    main()

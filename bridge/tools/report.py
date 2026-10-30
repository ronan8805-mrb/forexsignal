"""Measurement report from the bridge SQLite DB.

    python tools/report.py --db data/signals.db [--days 30]

Prints:
  * signal funnel: received -> rejected / vetoed / passed, by reason
  * AI shadow analysis: realised R of trades the AI WOULD have vetoed vs passed
    (this is how you decide whether to switch ai.mode from shadow to veto)
  * per-symbol / per-setup realised R from EA execution reports

Realised R per signal = sum(profit of CLOSE/PARTIAL events) / risk money reported
on OPEN. The EA reports r_multiple per closing deal; we sum those.
No numbers are invented here — if there are no execution reports, it says so.
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from collections import defaultdict


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/signals.db")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-score", type=float, default=55)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    since = time.time() - args.days * 86400

    print(f"=== Signal funnel (last {args.days} days) ===")
    for r in con.execute("SELECT status, COUNT(*) n FROM signals WHERE received_at>=? GROUP BY status", (since,)):
        print(f"  {r['status']:<10} {r['n']}")
    print("  top reject/veto reasons:")
    for r in con.execute(
        """SELECT COALESCE(reject_reason,'-') reason, COUNT(*) n FROM signals
           WHERE received_at>=? AND status IN ('rejected','vetoed') GROUP BY reason ORDER BY n DESC LIMIT 10""",
        (since,),
    ):
        print(f"    {r['n']:>4}  {r['reason'][:90]}")

    days = con.execute(
        "SELECT COUNT(DISTINCT date(received_at,'unixepoch')) FROM signals WHERE received_at>=? AND status='passed'",
        (since,),
    ).fetchone()[0]
    passed = con.execute("SELECT COUNT(*) FROM signals WHERE received_at>=? AND status='passed'", (since,)).fetchone()[0]
    if days:
        print(f"  passed signals per active day: {passed / days:.2f}")

    # Realised R per signal from execution reports
    rsum: dict[str, float] = defaultdict(float)
    have_exec = False
    for r in con.execute(
        "SELECT signal_id, r_multiple FROM executions WHERE event IN ('PARTIAL','CLOSE') AND r_multiple IS NOT NULL"
    ):
        have_exec = True
        rsum[r["signal_id"]] += r["r_multiple"]

    print("\n=== Realised results (from EA execution reports) ===")
    if not have_exec:
        print("  no execution reports yet — enable InpReportToBridge in the EA, or import the EA journal CSV.")
        return

    rows = con.execute(
        "SELECT signal_id, symbol, setup, ai_score, ai_verdict FROM signals WHERE received_at>=? AND status='passed'",
        (since,),
    ).fetchall()
    groups: dict[str, list[float]] = defaultdict(list)
    shadow = {"would_veto": [], "would_pass": []}
    for r in rows:
        if r["signal_id"] not in rsum:
            continue
        R = rsum[r["signal_id"]]
        groups[f"ALL"].append(R)
        groups[f"sym:{r['symbol']}"].append(R)
        groups[f"setup:{r['setup']}"].append(R)
        if r["ai_score"] is not None:
            shadow["would_veto" if r["ai_score"] < args.min_score else "would_pass"].append(R)

    def line(name: str, xs: list[float]) -> None:
        if not xs:
            return
        wins = sum(1 for x in xs if x > 0)
        gross_win = sum(x for x in xs if x > 0)
        gross_loss = -sum(x for x in xs if x < 0)
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
        print(f"  {name:<16} n={len(xs):<4} win%={100 * wins / len(xs):5.1f}  avgR={sum(xs) / len(xs):+.2f}"
              f"  totalR={sum(xs):+.1f}  PF={pf:.2f}")

    for k in sorted(groups):
        line(k, groups[k])
    print(f"\n=== AI shadow split (threshold {args.min_score}) ===")
    line("ai_would_pass", shadow["would_pass"])
    line("ai_would_veto", shadow["would_veto"])
    print("  Switch ai.mode to 'veto' only if ai_would_veto is clearly worse over a meaningful sample.")


if __name__ == "__main__":
    main()

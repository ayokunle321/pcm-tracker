#!/usr/bin/env python3
"""Compare two profiles and report what moved.

PCM contents are byte deterministic for a fixed compiler and corpus, so a
single measurement of each side is enough and any difference is real. That is
what lets a threshold this small be meaningful.

Exits 1 when a headline metric moves by more than the threshold, so a runner
loop can flag the commit.
"""

import argparse
import json
import sys


def pct(delta, base):
    return (delta / base * 100) if base else 0.0


def headline(name, a, b, width=22):
    if a is None or b is None:
        return None
    d = b - a
    return f"  {name:<{width}} {a:>14,d} -> {b:>14,d}  {d:>+13,d} B  {pct(d, a):+7.2f}%"


def section(title, a, b, counts_a, counts_b, limit, min_bytes):
    rows = []
    for k in set(a) | set(b):
        x, y = a.get(k, 0), b.get(k, 0)
        if x != y and abs(y - x) >= min_bytes:
            rows.append((y - x, k, x, y,
                         counts_a.get(k, 0), counts_b.get(k, 0)))
    if not rows:
        return
    rows.sort(key=lambda r: r[0])
    print(f"\n-- {title} --")
    for d, k, x, y, ca, cb in rows[:limit]:
        line = f"  {d:>+13,d} B  {pct(d, x):+7.2f}%  {k}"
        if ca or cb:
            line += f"   (records {ca:,} -> {cb:,})"
        print(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--threshold", type=float, default=0.1,
                    help="percent change that counts as a regression")
    # See the note in report.py. Peak memory carries about 1.11% of layout
    # noise on this corpus, so gating it at the byte threshold turns a coin
    # flip into a regression report.
    ap.add_argument("--rss-threshold", type=float, default=1.0,
                    help="percent change in peak memory that counts as a "
                         "regression")
    ap.add_argument("--rss-min-kb", type=int, default=2048,
                    help="smallest peak memory growth worth calling a "
                         "regression, above the 1,376 KB build quantum")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--min-bytes", type=int, default=64,
                    help="ignore moves smaller than this")
    args = ap.parse_args()

    a = json.load(open(args.before))
    b = json.load(open(args.after))

    print(f"=== {a.get('commit') or args.before} -> "
          f"{b.get('commit') or args.after} ===")
    if b.get("subject"):
        print(f"    {b['subject']}")
    for line in (headline("total pcm bytes", a["total_bytes"], b["total_bytes"]),
                 headline("loaded sloc bytes", a.get("loaded_sloc_bytes"),
                          b.get("loaded_sloc_bytes"))):
        if line:
            print(line)

    section("blocks that moved", a["blocks"], b["blocks"], {}, {},
            args.limit, args.min_bytes)
    section("records that moved", a["records"], b["records"],
            a.get("record_counts", {}), b.get("record_counts", {}),
            args.limit, args.min_bytes)

    # Peak memory counts as a headline metric, not a footnote. A change can
    # leave the bytes alone and still cost the writer a megabyte, and that is
    # worth stopping on.
    for phase in ("write", "read"):
        x = ((a.get("bench") or {}).get(phase) or {}).get("max_rss_kb")
        y = ((b.get("bench") or {}).get(phase) or {}).get("max_rss_kb")
        if x and y and x != y:
            print(f"  {phase + ' peak rss':<22} {x:>14,d} -> {y:>14,d}  "
                  f"{y - x:>+13,d} KB  {pct(y - x, x):+7.2f}%")

    worst = 0.0
    for key in ("total_bytes", "loaded_sloc_bytes"):
        if a.get(key) and b.get(key):
            worst = max(worst, pct(b[key] - a[key], a[key]))
    # Both tests have to pass. The percentage alone cannot separate the build
    # quantum from a real change, because the same absolute step is a different
    # proportion of the writer's peak and of the reader's.
    rss_worst = 0.0
    rss_kb = 0
    for phase in ("write", "read"):
        x = ((a.get("bench") or {}).get(phase) or {}).get("max_rss_kb")
        y = ((b.get("bench") or {}).get(phase) or {}).get("max_rss_kb")
        if x and y and pct(y - x, x) > rss_worst:
            rss_worst, rss_kb = pct(y - x, x), y - x
    if rss_worst > args.rss_threshold and rss_kb > args.rss_min_kb:
        print(f"\nREGRESSION: peak memory grew by {rss_kb:,} KB "
              f"({rss_worst:.2f}%), over the {args.rss_min_kb} KB and "
              f"{args.rss_threshold}% thresholds")
        return 1
    if worst > args.threshold:
        print(f"\nREGRESSION: grew by {worst:.2f}%, "
              f"over the {args.threshold}% threshold")
        return 1
    print(f"\nok: no metric grew by more than {args.threshold}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Measure what it costs to write and to read a PCM corpus.

Disk size is the least important of the numbers this tool collects. Writing
and reading modules costs time and memory, and those are what a serialization
change is usually judged on, so they are measured first and separately.

Wall time on a shared machine is not trustworthy, so instructions retired is
the headline time metric when perf is available. Peak RSS comes from the
kernel via /usr/bin/time and is not sensitive to load.

The writer and the reader are measured apart because a change can easily make
one cheaper and the other dearer.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

CC1 = ["-cc1", "-xc++", "-std=c++17", "-fmodules", "-fno-implicit-modules",
       "-fmodules-embed-all-files"]
PERF_EVENTS = ["instructions:u", "task-clock"]


def have_perf():
    if not shutil.which("perf"):
        return False
    r = subprocess.run(["perf", "stat", "-x", ";", "-e", "instructions:u",
                        "true"], capture_output=True, text=True)
    return r.returncode == 0


def run_once(cmd, use_perf):
    """Run cmd, returning peak RSS in KB, wall seconds and instructions."""
    wrapped = ["/usr/bin/time", "-f", "%M;%e"]
    if use_perf:
        wrapped += ["perf", "stat", "-x", ";"]
        for e in PERF_EVENTS:
            wrapped += ["-e", e]
    wrapped += cmd
    r = subprocess.run(wrapped, capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(r.stderr[-2000:] + "\n")
        return None
    rss, wall, insns = None, None, None
    for line in r.stderr.split("\n"):
        m = re.match(r"^(\d+);([\d.]+)$", line.strip())
        if m:
            rss, wall = int(m.group(1)), float(m.group(2))
        m = re.match(r"^([\d.]+);;instructions:u", line.strip())
        if m:
            insns = int(float(m.group(1)))
    return {"max_rss_kb": rss, "wall_s": wall, "instructions": insns}


def best_of(cmd, use_perf, runs):
    """Take the minimum, which is the run least disturbed by other load."""
    got = [r for r in (run_once(cmd, use_perf) for _ in range(runs)) if r]
    if not got:
        return None
    out = {}
    for k in ("max_rss_kb", "wall_s", "instructions"):
        vals = [g[k] for g in got if g.get(k) is not None]
        out[k] = min(vals) if vals else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clang")
    ap.add_argument("corpus")
    ap.add_argument("pcmdir")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with open(os.path.join(args.corpus, "corpus.json")) as f:
        n = json.load(f)["modules"]
    maps = [f"-fmodule-map-file={os.path.join(args.corpus, f'mod{k}.map')}"
            for k in range(n)]
    use_perf = have_perf()

    # Writing. The last module in the chain is the interesting one, since it
    # has the most already-written modules to reuse a file from.
    last = n - 1
    write_cmd = ([args.clang] + CC1 + maps +
                 [f"-fmodule-file={os.path.join(args.pcmdir, f'mod{last - 1}.pcm')}",
                  f"-fmodule-name=mod{last}", "-emit-module",
                  os.path.join(args.corpus, f"mod{last}.map"),
                  "-o", os.path.join(args.pcmdir, f"mod{last}.pcm")])

    # Reading. Load every module in the corpus and do nothing else.
    read_cmd = ([args.clang] + CC1 + maps +
                [f"-fmodule-file={os.path.join(args.pcmdir, f'mod{k}.pcm')}"
                 for k in range(n)] +
                ["-fsyntax-only", os.path.join(args.corpus, "check.cc")])

    data = {"perf": use_perf,
            "write": best_of(write_cmd, use_perf, args.runs),
            "read": best_of(read_cmd, use_perf, args.runs)}

    for phase in ("write", "read"):
        d = data[phase]
        if not d:
            print(f"  {phase}: failed")
            continue
        bits = [f"rss {d['max_rss_kb'] / 1024:.1f} MB",
                f"wall {d['wall_s']:.3f}s"]
        if d.get("instructions"):
            bits.append(f"{d['instructions']:,} insns")
        print(f"  {phase:<6} " + "  ".join(bits))

    if args.out:
        with open(args.out, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

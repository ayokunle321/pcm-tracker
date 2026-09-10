#!/usr/bin/env python3
"""Profile a directory of PCMs into a JSON record of where their bytes are.

Totals alone hide what moved. A change that drops source location entries and
a change that grows the preprocessor block both show up as a different file
size, so this keeps the per-block and per-record breakdown llvm-bcanalyzer
reports and lets the diff name the record kind that moved.
"""

import argparse
import collections
import glob
import json
import os
import re
import subprocess
import sys

# Bumped whenever a change to this file makes its output incomparable with
# what an earlier version wrote. The runner re-measures any commit whose
# record does not carry the current number, so a stale row cannot survive a
# walk and the report never has to reason about mixed data.
SCHEMA = 2

BLOCK = re.compile(r"\s*Block ID #\d+ \((\w+)\):")
SIZE = re.compile(r"\s*Total Size: (\d+)b")
# count, bits, bits/record, % abbreviated, record kind. A record kind with no
# abbreviation prints an empty column where the percentage goes, so that field
# has to be optional. Requiring it silently dropped every unabbreviated kind,
# which was 45% of all record bits.
RECORD = re.compile(
    r"^\s+(\d+)\s+(\d+)\s+[\d.]+\s+(?:[\d.]+\s+)?(\w+)\s*$")


def profile_one(bcanalyzer, path, blocks, records, counts):
    out = subprocess.run([bcanalyzer, path], capture_output=True,
                         text=True).stdout
    block = None
    for line in out.split("\n"):
        m = BLOCK.match(line)
        if m:
            block = m.group(1)
            continue
        m = SIZE.match(line)
        if m and block:
            blocks[block] += int(m.group(1))
            continue
        m = RECORD.match(line)
        if m and block:
            key = block + "/" + m.group(3)
            counts[key] += int(m.group(1))
            records[key] += int(m.group(2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pcmdir")
    ap.add_argument("out")
    ap.add_argument("--bcanalyzer", default="llvm-bcanalyzer")
    ap.add_argument("--loaded-sloc", type=int, default=None,
                    help="loaded source location bytes, from build_corpus.py")
    ap.add_argument("--commit", default=None)
    ap.add_argument("--subject", default=None)
    args = ap.parse_args()

    pcms = sorted(glob.glob(os.path.join(args.pcmdir, "*.pcm")))
    if not pcms:
        sys.stderr.write(f"no pcms in {args.pcmdir}\n")
        return 1

    blocks = collections.Counter()
    records = collections.Counter()
    counts = collections.Counter()
    for p in pcms:
        profile_one(args.bcanalyzer, p, blocks, records, counts)

    data = {
        "schema": SCHEMA,
        "commit": args.commit,
        "subject": args.subject,
        "pcms": len(pcms),
        "total_bytes": sum(os.path.getsize(p) for p in pcms),
        "loaded_sloc_bytes": args.loaded_sloc,
        # bcanalyzer reports bits; store bytes so the numbers read like sizes.
        "blocks": {k: v // 8 for k, v in blocks.items()},
        "records": {k: v // 8 for k, v in records.items()},
        "record_counts": dict(counts),
    }
    with open(args.out, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    print(f"{args.out}: {len(pcms)} pcms, {data['total_bytes']:,} B"
          + (f", {args.loaded_sloc:,} B loaded sloc"
             if args.loaded_sloc else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

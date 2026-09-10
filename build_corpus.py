#!/usr/bin/env python3
"""Build a synthetic corpus into PCMs with a given clang, and report the
source location space the result occupies when all of it is loaded.

Each module is built with -fmodule-file pointing at the previous one, so a
module can reuse a copy of a shared header an earlier module already wrote.
-fmodules-embed-all-files is passed because a file's embedded contents go away
with its source location entry, and that is where most of the size of a
deduplicated PCM is.
"""

import argparse
import json
import os
import re
import subprocess
import sys

CC1 = ["-cc1", "-xc++", "-std=c++17", "-fmodules", "-fno-implicit-modules",
       "-fmodules-embed-all-files"]


def module_count(corpus):
    with open(os.path.join(corpus, "corpus.json")) as f:
        return json.load(f)["modules"]


def build(clang, corpus, out):
    os.makedirs(out, exist_ok=True)
    n = module_count(corpus)
    maps = [f"-fmodule-map-file={os.path.join(corpus, f'mod{k}.map')}"
            for k in range(n)]
    pcms = []
    for k in range(n):
        pcm = os.path.join(out, f"mod{k}.pcm")
        cmd = [clang] + CC1 + maps
        if k:
            cmd.append(f"-fmodule-file={pcms[-1]}")
        cmd += [f"-fmodule-name=mod{k}", "-emit-module",
                os.path.join(corpus, f"mod{k}.map"), "-o", pcm]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode or not os.path.exists(pcm):
            sys.stderr.write(f"failed to build mod{k}\n{r.stderr[:2000]}\n")
            return None, None
        pcms.append(pcm)

    # Load every module at once and ask how much source location space it took.
    cmd = ([clang] + CC1 + maps + [f"-fmodule-file={p}" for p in pcms] +
           ["-fsyntax-only", os.path.join(corpus, "check.cc")])
    r = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r"(\d+)B \([^)]*\) in locations loaded from AST files",
                  r.stderr)
    loaded = int(m.group(1)) if m else None
    if loaded is None:
        sys.stderr.write("could not read sloc usage\n" + r.stderr[:2000] + "\n")
    return pcms, loaded


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clang")
    ap.add_argument("corpus")
    ap.add_argument("out")
    args = ap.parse_args()
    pcms, loaded = build(args.clang, args.corpus, args.out)
    if pcms is None:
        return 1
    total = sum(os.path.getsize(p) for p in pcms)
    print(f"{len(pcms)} pcms, {total:,} B on disk, "
          f"{loaded:,} B loaded sloc space" if loaded else
          f"{len(pcms)} pcms, {total:,} B on disk")
    return 0


if __name__ == "__main__":
    sys.exit(main())

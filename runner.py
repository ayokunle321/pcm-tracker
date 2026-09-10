#!/usr/bin/env python3
"""Walk a range of llvm-project commits, and record where each one puts the
bytes of a PCM corpus.

For each commit it checks the tree out, builds clang, builds the corpus, and
writes data/<sha>.json. A commit already in data/ is skipped, so the loop can
be stopped and restarted, and so a run over a range costs only the commits it
has not seen.

Most upstream commits cannot change a PCM at all, so by default only the ones
touching a path that can are measured. See DEFAULT_PATHS.

Building clang is the expensive part by a wide margin. Measuring is under a
second, which is why this can afford to look at every commit rather than
sampling.
"""

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", repo] + list(args),
                       capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


# The paths that can change what a PCM holds.
#
# This is an allowlist, and that is the point. A list of exclusions defaults
# every new directory to being measured, so clang/lib/CIR arrived and was
# measured for months of commits that lower an AST to ClangIR, which an
# -emit-module invocation never reaches. Naming what is in scope means a new
# directory has to argue its way in.
#
# The list is derived from what a PCM contains rather than from what sounds
# related:
#
#   Decls and types in DECLTYPES_BLOCK come from AST, which fixes their layout
#   and contents, from Sema, which decides which of them exist at all, and from
#   Parse, which decides what is parsed into them.
#
#   Source location entries and buffers in SOURCE_MANAGER_BLOCK come from Basic,
#   which owns SourceManager and FileManager, and from Lex, which decides who
#   enters a file. Macros in PREPROCESSOR_BLOCK come from Lex.
#
#   The control block holds language, target and header search options, which
#   Frontend builds out of the command line, and the predefines buffer and the
#   <module-includes> main file, which Frontend also creates.
#
#   Input files and module maps come from HeaderSearch and ModuleMap in Lex and
#   from FileManager in Basic.
#
#   Builtin headers are parsed like any other header, so clang/lib/Headers is in
#   scope, and TableGen emitters reach the attributes, builtins and diagnostics
#   through generated code.
#
#   The byte encoding itself, its abbreviations and its VBR widths, belongs to
#   llvm Bitstream. Not Bitcode, which is LLVM IR and unrelated.
#
# The few llvm Support files listed reach PCM bytes for specific reasons. The
# on disk hash table is the format of the header search and identifier tables,
# BLAKE3 computes the signature that is written into the file, DJB and Hashing
# fix the bucket order in those tables, Path holds the canonicalization the
# input file key rests on, and the virtual file system sits under file identity.
# They cost nothing to watch, none of them having changed in the last eight
# hundred commits.
#
# Everything else under clang/lib is downstream of the AST or beside it. CIR and
# CodeGen lower it, Driver never runs because the corpus invokes -cc1 directly,
# and StaticAnalyzer, Tooling, Format, Index, ExtractAPI, InstallAPI,
# Interpreter, Rewrite, ARCMigrate, CrossTU, Edit and APINotes are not loaded by
# a module or syntax only build.
#
# The claim about Driver, CIR and CodeGen is a claim about how the corpus is
# invoked. If bench.py ever drives the compiler through the driver or asks it to
# emit code, they belong back in the list.
DEFAULT_PATHS = [
    # Serialization itself.
    "clang/lib/Serialization/",
    "clang/include/clang/Serialization/",
    # What gets serialized, and what exists to be serialized.
    "clang/lib/AST/",
    "clang/include/clang/AST/",
    "clang/lib/Sema/",
    "clang/include/clang/Sema/",
    "clang/lib/Parse/",
    "clang/include/clang/Parse/",
    # Source locations, files, macros.
    "clang/lib/Lex/",
    "clang/include/clang/Lex/",
    "clang/lib/Basic/",
    "clang/include/clang/Basic/",
    # Options in the control block, predefines, the module includes buffer.
    "clang/lib/Frontend/",
    "clang/include/clang/Frontend/",
    # Headers a module parses, and the generated code behind attributes,
    # builtins and diagnostics.
    "clang/lib/Headers/",
    "clang/utils/TableGen/",
    # The container format.
    "llvm/lib/Bitstream/",
    "llvm/include/llvm/Bitstream/",
    # Specific llvm pieces that reach PCM bytes.
    "llvm/include/llvm/Support/OnDiskHashTable.h",
    "llvm/include/llvm/Support/BLAKE3.h",
    "llvm/lib/Support/BLAKE3",
    "llvm/include/llvm/Support/DJB.h",
    "llvm/lib/Support/DJB.cpp",
    "llvm/include/llvm/ADT/Hashing.h",
    "llvm/include/llvm/Support/Path.h",
    "llvm/lib/Support/Path.cpp",
    "llvm/include/llvm/Support/VirtualFileSystem.h",
    "llvm/lib/Support/VirtualFileSystem.cpp",
]


def commits_in(repo, rev_range, paths):
    args = ["rev-list", "--reverse", rev_range]
    if paths:
        args += ["--"] + list(paths)
    out = git(repo, *args)
    return [c for c in out.split("\n") if c]


def current_schema(path):
    """Whether a stored measurement was written by the profiler we ship now.

    A measurement from an older profiler is not comparable with a current one,
    and a report that mixes the two has to explain itself. Re-measuring is
    cheap next to the clang build that precedes it, so staleness is settled
    here rather than described later.
    """
    sys.path.insert(0, HERE)
    import profile as profiler
    try:
        with open(path) as f:
            return json.load(f).get("schema") == profiler.SCHEMA
    except (OSError, ValueError):
        return False


def measure(args, sha, subject):
    out_json = os.path.join(args.data, sha[:12] + ".json")
    if os.path.exists(out_json) and not args.force:
        if current_schema(out_json):
            print(f"  already measured, skipping")
            return out_json, True
        print(f"  measured by an older profiler, re-measuring")

    git(args.repo, "checkout", "-q", sha)
    t = time.time()
    r = subprocess.run(["ninja", "-C", args.build, "clang", "llvm-bcanalyzer"],
                       capture_output=True, text=True)
    if r.returncode:
        tail = "\n".join(r.stdout.split("\n")[-15:])
        print(f"  BUILD FAILED\n{tail}")
        return None, False
    print(f"  built clang in {time.time() - t:.0f}s")

    clang = os.path.join(args.build, "bin", "clang")
    bcan = os.path.join(args.build, "bin", "llvm-bcanalyzer")
    pcmdir = os.path.join(args.workdir, "pcms")

    sys.path.insert(0, HERE)
    import build_corpus
    pcms, loaded = build_corpus.build(clang, args.corpus, pcmdir)
    if pcms is None:
        print("  CORPUS BUILD FAILED")
        return None, False

    cmd = [sys.executable, os.path.join(HERE, "profile.py"), pcmdir, out_json,
           "--bcanalyzer", bcan, "--commit", sha[:12], "--subject", subject]
    if loaded:
        cmd += ["--loaded-sloc", str(loaded)]
    subprocess.run(cmd, check=True)

    # Writing and reading cost time and memory, which matter more than bytes.
    # The instruction counts stay empty until perf is available, rather than
    # the metric being left out of the record.
    bench_json = out_json + ".bench"
    subprocess.run([sys.executable, os.path.join(HERE, "bench.py"), clang,
                    args.corpus, pcmdir, "--runs", str(args.runs),
                    "--out", bench_json], check=False)
    if os.path.exists(bench_json):
        with open(out_json) as f:
            data = json.load(f)
        with open(bench_json) as f:
            data["bench"] = json.load(f)
        with open(out_json, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.remove(bench_json)
    return out_json, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("range", help="git revision range, e.g. main~10..main")
    ap.add_argument("--repo", default="/home/ayo/llvm-project")
    ap.add_argument("--build", default="/home/ayo/llvm-project/build")
    ap.add_argument("--corpus", default=os.path.join(HERE, "workload/corpus"))
    ap.add_argument("--data", default=os.path.join(HERE, "data"))
    ap.add_argument("--workdir", default="/tmp/pcm-tracker")
    ap.add_argument("--threshold", type=float, default=0.1)
    ap.add_argument("--rss-threshold", type=float, default=1.0)
    ap.add_argument("--rss-min-kb", type=int, default=2048)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--paths", nargs="*", default=DEFAULT_PATHS,
                    help="measure only commits touching these paths. Pass the "
                         "flag with no value to measure every commit")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.data, exist_ok=True)
    os.makedirs(args.workdir, exist_ok=True)

    start_ref = git(args.repo, "rev-parse", "--abbrev-ref", "HEAD")
    if start_ref == "HEAD":
        start_ref = git(args.repo, "rev-parse", "HEAD")
    # Untracked files survive a checkout, so only tracked changes are a problem.
    dirty = git(args.repo, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        sys.stderr.write("repo has uncommitted changes to tracked files, "
                         "commit or stash first\n")
        return 1

    shas = commits_in(args.repo, args.range, args.paths)
    total = len(commits_in(args.repo, args.range, None))
    if args.paths and len(shas) != total:
        print(f"{total} commits in {args.range}, {len(shas)} of them touch a "
              f"path that can change a PCM")
    else:
        print(f"{total} commits in {args.range}, measuring every one")
    print(f"returning to {start_ref} after\n")

    results = []
    try:
        for i, sha in enumerate(shas, 1):
            subject = git(args.repo, "log", "-1", "--format=%s", sha)
            print(f"[{i}/{len(shas)}] {sha[:12]} {subject[:60]}")
            path, cached = measure(args, sha, subject)
            if path:
                results.append(path)
            if len(results) >= 2:
                subprocess.run(
                    [sys.executable, os.path.join(HERE, "diff.py"),
                     results[-2], results[-1],
                     "--threshold", str(args.threshold),
                     "--rss-threshold", str(args.rss_threshold),
                     "--rss-min-kb", str(args.rss_min_kb)])
            print()
    finally:
        git(args.repo, "checkout", "-q", start_ref, check=False)
        print(f"returned to {start_ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

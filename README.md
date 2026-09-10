# pcm-tracker

Walks a range of llvm-project commits and records where the bytes of a set of
Clang module files go, so a change in PCM size or in the cost of writing and
reading them can be attributed to the commit that caused it.

The measurement is cheap. Building clang at each commit is not, which is why
only commits that can change a PCM are built.

## What it measures

For every commit it builds clang, builds a synthetic `-fmodules` corpus, and
records

- total PCM bytes on disk
- loaded source location bytes, from `#pragma clang __debug sloc_usage`
- peak memory of the writer and of the reader, separately
- instructions retired for each, when `perf` is available
- the size of every block and every record kind, from `llvm-bcanalyzer`

The writer and the reader are measured apart because a change can easily make
one cheaper and the other dearer.

## Layout

    workload/generate.py   emits the corpus, 24 sibling modules over a chain
                           of shared headers, with templates and macros
    build_corpus.py        builds the corpus and reads the sloc usage
    profile.py             one PCM set to a JSON record of where its bytes are
    bench.py               peak memory and instruction counts, writer and
                           reader separately
    diff.py                two records, what moved, and whether to flag it
    runner.py              walks a commit range, building and measuring
    report.py              the records to a page
    refresh.sh             re-renders the page whenever a measurement lands

## Running it

    python3 workload/generate.py workload/corpus
    python3 runner.py 'HEAD~800..HEAD' --repo /path/to/llvm-project \
                                       --build /path/to/llvm-project/build
    python3 report.py --order 'HEAD~800..HEAD' --repo /path/to/llvm-project
    python3 -m http.server 8899 --directory site

`--repo` and `--build` default to paths that suit the machine this was written
on, so pass your own.

## Which commits get built

Most upstream commits cannot change a PCM at all. `DEFAULT_PATHS` in
`runner.py` lists the paths that can, and it is an allowlist rather than a list
of exclusions on purpose. A list of exclusions defaults every new directory to
being measured, which is how `clang/lib/CIR` came to be built for months of
commits that lower an AST to ClangIR, something `-emit-module` never reaches.

Over an eight hundred commit window this selects about six percent of commits.
The reasoning behind each entry, and the condition under which the exclusions
stop being safe, is in the comment above the list.

## Thresholds, and why they differ

PCM contents are byte deterministic for a fixed compiler and corpus, so one
measurement per commit is enough and a tenth of a percent is real.

Peak memory is not like that. It is exactly reproducible for a given build,
twenty five runs of one binary gave the same peak every time, but it moves by a
fixed 1,376 KB between builds for reasons unrelated to the commit. The same
absolute step is 1.11% of the writer's peak and 2.08% of the reader's, so no
single percentage separates it from a real change. Memory therefore has to
clear both a percentage and an absolute floor above one quantum before it
counts.

A change to peak memory smaller than about 3 MB on this corpus should not be
believed without building the same commit several times.

## Notes

`perf` is not installed on the machine this was built on and
`kernel.perf_event_paranoid` is 4, so the instruction columns are empty. They
fill in once `perf` works, and nothing else needs to change.

Measurements carry a `schema` number. The runner re-measures any commit whose
record does not carry the current one, so a change to the profiler cannot leave
the data half old and half new.

# pcm-tracker

Tracks changes in Clang module (`.pcm`) files across LLVM commits.

The workload is a synthetic `-fmodules` workload consisting of **24 sibling
modules** built over a chain of shared headers. The headers include
**templates and macros** to exercise common sources of module serialization
and source-location data.

## What it measures

For each selected commit, `pcm-tracker` records:

- **Total PCM size** on disk
- **Loaded source-location (`SLoc`) bytes**
- **Peak memory** for writing and reading PCMs, measured separately
- **Instructions retired** for writing and reading.
- **Size of each PCM block and record kind**, using `llvm-bcanalyzer`
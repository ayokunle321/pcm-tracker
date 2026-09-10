#!/usr/bin/env python3
"""Emit one synthetic -fmodules corpus for PCM tracking.

There is a single workload rather than a family of narrow ones, so that a
number from it means "modules got cheaper or dearer" rather than "one
mechanism moved". It carries the things a real modular build carries:

  templates and instantiations  -> DECLTYPES_BLOCK, the largest block in a
                                   real corpus
  macros and macro expansions   -> PREPROCESSOR_BLOCK and expansion entries
  a deep include graph          -> shared headers include each other, so a
                                   module pulls in a chain rather than a flat
                                   set
  headers shared textually      -> the same header enters many modules, which
                                   is where duplication comes from

The modules are siblings, built in a chain with -fmodule-file. They must not
include one another. A module that imports another already sees its include
guards, so a textual include of the same header is skipped and no local
source location is created for it.
"""

import argparse
import json
import os
import shutil

LEAF = """#ifndef LEAF_{i}_H
#define LEAF_{i}_H
{include_prev}

// Macros, so the corpus has a preprocessor block and macro expansion entries
// rather than only file entries.
#define LEAF{i}_JOIN(a, b) a##b
#define LEAF{i}_WRAP(x) ((x) + {i})
#define LEAF{i}_DECLARE(n) int LEAF{i}_JOIN(decl_, n)(int);

namespace leaf{i} {{

template <class T> struct Box {{
  T v;
  T get() const {{ return LEAF{i}_WRAP(v); }}
  template <class U> U convert() const {{ return static_cast<U>(v); }}
}};

template <class T, int N> struct Array {{
  T data[N];
  T sum() const {{
    T acc = T();
    for (int k = 0; k < N; ++k)
      acc += data[k];
    return acc;
  }}
}};

{decls}

{macro_uses}

inline int total{i}() {{ return LEAF{i}_WRAP({i}); }}

}} // namespace leaf{i}
#endif
"""

MOD = """{includes}

namespace mod{k} {{

{instantiations}

inline int entry{k}() {{ return {sums}; }}

}} // namespace mod{k}
"""


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--modules", type=int, default=24,
                    help="sibling modules, each writing one PCM")
    ap.add_argument("--depth", type=int, default=8,
                    help="length of the shared header include chain")
    ap.add_argument("--decls", type=int, default=120,
                    help="declarations per shared header")
    args = ap.parse_args()

    out = args.outdir
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)

    # A chain of shared headers, each including the one below it, so a module
    # that includes the top pulls in the whole depth.
    for i in range(args.depth):
        decls = "\n".join(
            f"struct Rec{i}_{j} {{ int a; long b; double c; Box<int> d; }};\n"
            f"int fn{i}_{j}(Rec{i}_{j} *p, Array<long, 4> a);"
            for j in range(args.decls))
        macro_uses = "\n".join(f"LEAF{i}_DECLARE({j})"
                               for j in range(args.decls // 4))
        write(os.path.join(out, f"leaf{i}.h"),
              LEAF.format(i=i, decls=decls, macro_uses=macro_uses,
                          include_prev=(f'#include "leaf{i - 1}.h"'
                                        if i else "")))

    top = args.depth - 1
    for k in range(args.modules):
        inst = "\n".join(
            f"inline leaf{i}::Box<long> box{k}_{i}() {{ return {{}}; }}\n"
            f"inline long arr{k}_{i}() {{ return leaf{i}::Array<long, 4>().sum(); }}"
            for i in range(args.depth))
        sums = " + ".join(f"leaf{i}::total{i}()" for i in range(args.depth))
        write(os.path.join(out, f"mod{k}.h"),
              MOD.format(k=k, includes=f'#include "leaf{top}.h"',
                         instantiations=inst, sums=sums))
        write(os.path.join(out, f"mod{k}.map"),
              f'module mod{k} {{ header "mod{k}.h" export * }}\n')

    write(os.path.join(out, "check.cc"),
          "#pragma clang __debug sloc_usage 100000\n")
    with open(os.path.join(out, "corpus.json"), "w") as f:
        json.dump({"modules": args.modules, "depth": args.depth,
                   "decls": args.decls}, f)
    print(f"{out}: {args.modules} modules, include chain of {args.depth}, "
          f"{args.decls} decls per header")


if __name__ == "__main__":
    main()

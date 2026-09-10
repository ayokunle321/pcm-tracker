#!/usr/bin/env python3
"""Render the measured history as a page.

One row per measured commit, oldest first, so the history reads downwards.
Commits that cannot change a PCM are not built, so a row can stand for itself
and the ones passed over since the row above, and says how many those were.
Each row carries the cost of writing and of reading the corpus, and the size
of the result. A row whose numbers moved past the threshold is marked, and clicking
it opens the block and record breakdown for that commit against the one
before it, which is the part that says what actually moved.

Instruction counts keep their columns even when perf is unavailable, so the
metric stays part of the layout and fills in once perf works.
"""

import argparse
import glob
import html
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from runner import DEFAULT_PATHS

CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a18;--dim:#6b6b66;--line:#e3e3df;--card:#fff;
--up:#a4262c;--down:#137333;--accent:#2b5797}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{padding:22px 26px 14px;border-bottom:1px solid var(--line);background:var(--card)}
h1{margin:0 0 4px;font-size:17px;font-weight:600;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:12.5px}
main{padding:20px 26px 60px;max-width:1500px}
table{width:100%;table-layout:fixed;border-collapse:collapse;
background:var(--card);border:1px solid var(--line);border-radius:6px;
overflow:hidden}
th,td{padding:6px 8px;text-align:right;white-space:nowrap;overflow:hidden;
border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
/* Widths are given in em, so a column asks for the room its text needs rather
   than a number of pixels that happens to suit one screen. The subject column
   is given no width at all, so it takes whatever is left over, which is a lot
   on a wide window and a little on a narrow one. Fixed layout means the table
   is always exactly as wide as its container, so the page never scrolls
   sideways. */
col.c-sha{width:7.5em}
col.c-metric{width:10.4rem}
th{background:#f6f6f4;font-size:11px;font-weight:600;color:var(--dim);
text-transform:uppercase;letter-spacing:.04em;text-align:right}
th.l,td.l{text-align:left}
tr.commit{cursor:pointer}
tr.commit:hover{background:#f7f9fc}
td.sha{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;
color:var(--accent)}
td.sha a{color:var(--accent);text-decoration:none;border-bottom:1px solid transparent}
td.sha a:hover{border-bottom-color:var(--accent)}
a.pr{color:var(--accent);text-decoration:none}
a.pr:hover{text-decoration:underline}
td.subj{overflow:hidden;text-overflow:ellipsis;color:var(--fg)}
.up{color:var(--up);font-weight:600}
.down{color:var(--down);font-weight:600}
.pending{color:#b9b9b4}
/* A metric is one block of a fixed width, holding the value and then the
   change. Header and cell centre the same width in the same column, so the
   two share a centre line and no column can drift from another.
   The widths are in rem rather than em on purpose. An em would resolve
   against each element's own font size, and the header sets a smaller one
   than the cell, so the header block would come out a different width from
   the block it is supposed to sit above. */
td.m,th.mh{text-align:center}
.mw{display:inline-block;width:9.2rem;text-align:right;white-space:nowrap}
th.mh .mw{text-align:center}
td.m{font-size:11px}
td.m .v{display:inline-block;width:4.2rem;text-align:right;overflow:hidden}
td.m .d{display:inline-block;width:4.8rem;margin-left:.15rem;text-align:right;
font-size:9px}
.gap{color:#9a9a94;font-weight:600;font-size:11.5px}
.grp{border-left:1px solid var(--line)}
tr.detail>td{background:#fafaf8;padding:0;border-bottom:1px solid var(--line)}
tr.detail.hide{display:none}
.inner{padding:14px 18px 18px}
.inner h3{margin:10px 0 6px;font-size:11px;text-transform:uppercase;
letter-spacing:.04em;color:var(--dim);font-weight:600}
.brk{width:auto;border:0;background:none}
.brk td{border:0;padding:2px 14px 2px 0;font-size:12.5px}
.none{color:var(--dim);font-style:italic;padding:4px 0}
code{background:#f0f0ec;padding:1px 5px;border-radius:3px;font-size:12px}
"""

JS = """
document.querySelectorAll('tr.commit').forEach(function(r){
  r.addEventListener('click', function(){
    var d = document.getElementById('d-' + r.dataset.i);
    if (d) d.classList.toggle('hide');
  });
});
"""


def short(n):
    """A signed count narrow enough to sit in a fixed gutter."""
    a = abs(n)
    for div, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{n / div:+.1f}{suffix}"
    return f"{n:+.0f}"


def rss_value(kb):
    return f"{kb / 1024:.1f} MB"


def rss_delta(d):
    return f"{d / 1024:+.1f}"


def count_value(n):
    return f"{n:,}"


def metric_cell(cur, prev, threshold, value, delta, grp=False, min_abs=0):
    """A metric and, in a gutter of its own, how it moved.

    The value and the change live in separate spans and the gutter is always
    the same width, so values line up down the column instead of shifting
    sideways whenever a neighbour gains a change to report.

    A change at or under the threshold is left out rather than printed as a
    zero. Most commits move nothing, and a column of zeroes reads as data
    while saying nothing, which makes the few real movements harder to see.

    Some metrics also carry a floor. Peak memory moves in a fixed quantum that
    has nothing to do with the commit, so for that one a percentage alone
    cannot tell a real change from the quantum, and the absolute size of the
    change has to clear it too.
    """
    cls = "m grp" if grp else "m"
    if cur is None:
        return (f'<td class="{cls}"><span class="mw">'
                f'<span class="v pending">-</span>'
                f'<span class="d"></span></span></td>')
    moved = ""
    if prev:
        d = cur - prev
        p = d / prev * 100
        if abs(p) > threshold and abs(d) > min_abs:
            way = "up" if d > 0 else "down"
            moved = f'<span class="{way}">{delta(d)} ({p:+.1f}%)</span>'
    return (f'<td class="{cls}"><span class="mw">'
            f'<span class="v">{value(cur)}</span>'
            f'<span class="d">{moved}</span></span></td>')


def github_url(repo):
    """The https form of the repository the commits came from.

    Prefer the upstream remote over origin, since a fork's origin would send a
    reader to a copy of the commit rather than to the one everybody else sees.
    """
    for remote in ("upstream", "origin"):
        r = subprocess.run(["git", "-C", repo, "remote", "get-url", remote],
                           capture_output=True, text=True)
        if r.returncode:
            continue
        url = r.stdout.strip()
        if url.startswith("git@github.com:"):
            url = "https://github.com/" + url[len("git@github.com:"):]
        if url.endswith(".git"):
            url = url[:-4]
        if "github.com" in url:
            return url
    return None


def local_only(repo, order_range):
    """Commits in the range that no remote has, so they have nowhere to link.

    A squashed measurement commit built on top of upstream lives only here, and
    a link to it would land on a page that does not exist.
    """
    for ref in ("upstream/main", "origin/main"):
        if subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "-q", ref],
                          capture_output=True).returncode:
            continue
        r = subprocess.run(["git", "-C", repo, "rev-list", order_range, "^" + ref],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return {c[:12] for c in r.stdout.split()}
    return set()


PR = re.compile(r"\(#(\d+)\)")


def link_subject(subject, url):
    """Turn the pull request number a commit subject carries into a link."""
    escaped = html.escape(subject or "")
    if not url:
        return escaped
    return PR.sub(
        lambda m: f'(<a class="pr" href="{url}/pull/{m.group(1)}" '
                  f'target="_blank" rel="noopener">#{m.group(1)}</a>)',
        escaped)


def breakdown(cur, prev, key, limit, min_bytes):
    a, b = prev.get(key, {}), cur.get(key, {})
    ca = prev.get("record_counts", {})
    cb = cur.get("record_counts", {})
    rows = []
    for k in set(a) | set(b):
        x, y = a.get(k, 0), b.get(k, 0)
        if x != y and abs(y - x) >= min_bytes:
            rows.append((y - x, k, x, y, ca.get(k, 0), cb.get(k, 0)))
    rows.sort(key=lambda r: r[0])
    if not rows:
        return '<div class="none">nothing moved</div>'
    out = ['<table class="brk">']
    for d, k, x, y, na, nb in rows[:limit]:
        p = (d / x * 100) if x else 0
        cls = "up" if d > 0 else "down"
        rec = (f"records {na:,} &rarr; {nb:,}" if (na or nb) else "")
        out.append(f'<tr><td class="{cls}">{d:+,} B</td>'
                   f'<td class="{cls}">{p:+.2f}%</td>'
                   f'<td class="l">{html.escape(k)}</td>'
                   f'<td class="l" style="color:#6b6b66">{rec}</td></tr>')
    out.append("</table>")
    return "".join(out)


def load(data_dir, repo, order_range, paths):
    files = glob.glob(os.path.join(data_dir, "*.json"))
    rows = {}
    for f in files:
        d = json.load(open(f))
        if d.get("commit"):
            rows[d["commit"]] = d
    if order_range and repo:
        argv = ["git", "-C", repo, "rev-list", "--reverse", order_range]
        if paths:
            argv += ["--"] + list(paths)
        r = subprocess.run(argv, capture_output=True, text=True)
        if r.returncode == 0:
            # Only commits that can change a PCM reach this list, so a gap
            # in it is a commit that could have moved the numbers and was not
            # measured. A row carries that count, so a delta standing for more
            # than one commit says so rather than reading as one commit's work.
            out, gap = [], 0
            for sha in (c[:12] for c in r.stdout.split()):
                if sha in rows:
                    out.append((rows[sha], gap))
                    gap = 0
                else:
                    gap += 1
            return out
    return [(rows[k], 0) for k in sorted(rows, key=lambda k:
                                         os.path.getmtime(os.path.join(
                                             data_dir, k + ".json")))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data"))
    # GitHub Pages will serve a branch's root or its docs directory and
    # nothing else, so the page is written where it can be published from.
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "docs", "index.html"))
    ap.add_argument("--repo", default="/home/ayo/llvm-project")
    ap.add_argument("--order", default=None,
                    help="git range fixing row order, e.g. base..HEAD")
    ap.add_argument("--threshold", type=float, default=0.1)
    # Peak memory is not deterministic the way the bytes are. Across commits
    # that change nothing in the output, the writer's peak has been observed
    # taking one of two values 1,376 KB apart, which is 1.11%, depending on how
    # the binary happens to be laid out. Bytes get to keep a tenth of a percent
    # because they really are reproducible. Memory needs room above that
    # spread, or every other commit reads as a regression.
    ap.add_argument("--rss-threshold", type=float, default=1.0,
                    help="percent change in peak memory that counts")
    # The noise in peak memory is a fixed size, not a fixed proportion. The
    # same 1,376 KB step appears on the writer, where it is 1.11%, and on the
    # reader, where the smaller base makes it 2.08%, so no single percentage
    # separates it from a real change. Twenty five runs of one binary gave the
    # same peak every time, so the quantum is a property of the build rather
    # than of the run, and repeating a measurement will not average it away.
    # A floor above one quantum will.
    ap.add_argument("--rss-min-kb", type=int, default=2048,
                    help="smallest peak memory change worth reporting, set "
                         "above the observed 1,376 KB build to build quantum")
    ap.add_argument("--limit", type=int, default=5,
                    help="how many commit rows to render, newest last. "
                         "Zero or less renders every measured commit")
    ap.add_argument("--breakdown-limit", type=int, default=14,
                    help="how many block or record entries a row's "
                         "breakdown lists")
    ap.add_argument("--min-bytes", type=int, default=64)
    ap.add_argument("--paths", nargs="*", default=DEFAULT_PATHS,
                    help="show only commits touching these paths. Pass the "
                         "flag with no value to show every measured commit")
    args = ap.parse_args()

    rows = load(args.data, args.repo, args.order, args.paths)
    url = github_url(args.repo)
    unlinkable = local_only(args.repo, args.order) if args.order else set()
    if not rows:
        sys.stderr.write(f"no measurements in {args.data}\n")
        return 1

    body = []
    body.append('<table><colgroup><col class="c-sha"><col>'
                + '<col class="c-metric">' * 6
                + '</colgroup><thead><tr>'
                '<th class="l">commit</th><th class="l">subject</th>'
                '<th class="grp mh"><span class="mw">write rss</span></th>'
                '<th class="mh"><span class="mw">write insns</span></th>'
                '<th class="grp mh"><span class="mw">read rss</span></th>'
                '<th class="mh"><span class="mw">read insns</span></th>'
                '<th class="grp mh"><span class="mw">pcm bytes</span></th>'
                '<th class="grp mh"><span class="mw">loaded sloc</span></th>'
                '</tr></thead><tbody>')

    series = []
    earlier = None
    for r, gap in rows:
        series.append((r, gap, earlier))
        earlier = r
    visible = series[-args.limit:] if args.limit > 0 else series

    for i, (r, gap, prev) in enumerate(visible):
        bench = r.get("bench") or {}
        w = bench.get("write") or {}
        rd = bench.get("read") or {}
        body.append(f'<tr class="commit" data-i="{i}">')
        gap_note = ""
        if gap:
            gap_note = (f' <span class="gap" title="{gap} commit'
                        f'{"" if gap == 1 else "s"} between this row and the '
                        f'one above it could have changed a PCM and was not '
                        f'measured">+{gap}</span>')
        sha = r["commit"] or "?"
        if url and sha not in unlinkable and sha != "?":
            shown = (f'<a href="{url}/commit/{sha}" target="_blank" '
                     f'rel="noopener">{html.escape(sha)}</a>')
        else:
            shown = (f'<span title="not on any remote, so there is nothing to '
                     f'link to">{html.escape(sha)}</span>')
        body.append(f'<td class="l sha">{shown}{gap_note}</td>')
        subject = r.get("subject") or ""
        body.append(f'<td class="l subj" title="{html.escape(subject, True)}">'
                    f'{link_subject(subject, url)}</td>')
        pb = (prev.get("bench") or {}) if prev else {}
        pw, pr = pb.get("write") or {}, pb.get("read") or {}
        for cur, old_ in ((w, pw), (rd, pr)):
            body.append(metric_cell(cur.get("max_rss_kb"),
                                    old_.get("max_rss_kb"),
                                    args.rss_threshold,
                                    rss_value, rss_delta, grp=True,
                                    min_abs=args.rss_min_kb))
            body.append(metric_cell(cur.get("instructions"),
                                    old_.get("instructions"), args.threshold,
                                    count_value, short))
        for key in ("total_bytes", "loaded_sloc_bytes"):
            body.append(metric_cell(r.get(key),
                                    prev.get(key) if prev else None,
                                    args.threshold, count_value, short,
                                    grp=True))
        body.append("</tr>")

        inner = ['<div class="inner">']
        if prev:
            inner.append("<h3>blocks</h3>")
            inner.append(breakdown(r, prev, "blocks", args.breakdown_limit,
                                   args.min_bytes))
            inner.append("<h3>records</h3>")
            inner.append(breakdown(r, prev, "records", args.breakdown_limit,
                                   args.min_bytes))
        else:
            inner.append('<div class="none">first measurement, '
                         'nothing to compare against</div>')
        inner.append("</div>")
        body.append(f'<tr class="detail hide" id="d-{i}"><td colspan="8">'
                    + "".join(inner) + "</td></tr>")
    body.append("</tbody></table>")

    showing = (f"{len(rows)} commits measured"
               if len(visible) == len(rows) else
               f"the {len(visible)} most recent of {len(rows)} "
               f"commits measured")

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PCM tracker</title><style>{CSS}</style></head><body>
<header>
<h1>PCM tracker</h1>
<div class="sub">{showing} on one synthetic <code>-fmodules</code> corpus.
Click a row for the block and record breakdown against the commit before it.
</div>
</header>
<main>{''.join(body)}</main>
<script>{JS}</script></body></html>"""

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        f.write(page)
    print(f"{args.out}: {len(rows)} commits")
    return 0


if __name__ == "__main__":
    sys.exit(main())

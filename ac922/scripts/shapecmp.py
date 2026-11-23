#!/usr/bin/env python3
"""Compare concscale arms when the lane is not quiet.

The 4-card lane now shares the box with the operator's NNLC job array, so raw arm-vs-arm aggregates are
not comparable: one cell measured while the co-tenant held 19 cores is worth a different amount than the
same cell at 8 cores.  Every concscale cell carries `foreign_cores`, so arms are compared only between
cells whose co-tenant load matches, and a linear fit in foreign_cores is reported next to the matched pairs
so the reader can see both.

Usage: shapecmp.py [--tol CORES] [--min-tokens N] ARM=PATH [ARM=PATH ...]
"""
import argparse, json, os, re, statistics as S, sys


def med(v):
    v = [x for x in v if x is not None and x == x]
    return S.median(v) if v else None


def load(path):
    out = []
    with open(path, errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            out.append(r)
    return out


def fit(pts, x_at=None):
    """Least squares agg = a + b*foreign over (foreign, agg) pairs; predict at x_at."""
    if len(pts) < 2:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    mx, my = S.mean(xs), S.mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    a = my - b * mx
    r2 = 0.0
    ss = sum((y - my) ** 2 for y in ys)
    if ss > 0:
        r2 = 1 - sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys)) / ss
    return {"a": round(a, 2), "b": round(b, 2), "r2": round(r2, 2),
            "at": {str(x_at[i]): round(a + b * x_at[i], 1) for i in range(len(x_at))} if x_at else {}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arms", nargs="+")
    ap.add_argument("--tol", type=float, default=4.0)
    ap.add_argument("--min-tokens", type=int, default=1500)
    a = ap.parse_args()
    xq = [0.0, 8.0, 12.0, 18.0]
    data = {}
    for spec in a.arms:
        name, path = spec.split("=", 1)
        rows = load(path)
        keep = [r for r in rows if r.get("errors", 1) == 0 and r.get("tokens_total", 0) >= a.min_tokens
                and r.get("foreign_cores") is not None]
        data[name] = keep
        print("%-8s cells=%d kept=%d  (dropped: errors or thin cells)" % (name, len(rows), len(keep)))
    ns = sorted({r["n"] for v in data.values() for r in v})
    print("\n%-4s %-9s %s" % ("n", "arm", "agg_med  foreign_med  step_ms  rows/step  cells  fit agg@0/8/12/18 (r2)"))
    fits = {}
    for n in ns:
        for name, rows in data.items():
            sel = [r for r in rows if r["n"] == n]
            if not sel:
                continue
            f = fit([(r["foreign_cores"], r["agg_wall"]) for r in sel], xq)
            fits[(name, n)] = f
            print("%-4d %-9s %8.1f %11.1f %9s %10s %6d  %s" % (
                n, name, med([r["agg_wall"] for r in sel]), med([r["foreign_cores"] for r in sel]),
                med([r.get("cen_step_ms") for r in sel]), med([r.get("cen_rows_step") for r in sel]), len(sel),
                ("-" if f is None else " ".join("%s=%.1f" % (k, v) for k, v in f["at"].items()) + " (r2 %.2f)" % f["r2"])))
    names = list(data)
    if len(names) < 2:
        return
    base, alt = names[0], names[1]
    print("\nmatched pairs (%s vs %s, |foreign| <= %.1f cores):" % (alt, base, a.tol))
    for n in ns:
        ratios, used = [], []
        for r2 in [r for r in data[alt] if r["n"] == n]:
            for r1 in [r for r in data[base] if r["n"] == n]:
                if abs(r1["foreign_cores"] - r2["foreign_cores"]) <= a.tol:
                    ratios.append(r2["agg_wall"] / r1["agg_wall"])
                    used.append((r1["foreign_cores"], r2["foreign_cores"]))
        if ratios:
            print("  n=%d  pairs=%d  agg ratio median %.3f  (min %.3f max %.3f)" % (
                n, len(ratios), S.median(ratios), min(ratios), max(ratios)))
        else:
            print("  n=%d  no matched pair within %.1f cores" % (n, a.tol))
    print("\nfit-based extrapolation to a quiet lane (foreign=0) is the honest arm-vs-arm number; the "
          "matched pairs are the interpolation.")


main()

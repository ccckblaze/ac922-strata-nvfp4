"""Read the decode window anatomy the engine already prints, for one boot, bucketed by how many
rows actually shared a window.

`strata batch: <n> windows, avg <r> rows, <ms> ms/window = run .. (CUDA0 GPU-reach wait .. + CPU
experts ..) + commit .. + emit .. + adapt wait ..; per stage ..; per layer-window ..; routed
entries per window: VRAM .. (..%), PCIe .. (..%), CPU .. (..%); .. rows/s over .. ms`

avg rows is the sharing degree actually achieved - the single number that says whether a window
shape paid off. Numbers only; nothing from the model's text is read.

usage: window_anatomy.py [--boot last|all] [--eng PATH]
"""
import argparse, re, statistics as st

PAT = re.compile(
    r"strata batch: (\d+) windows, avg ([\d.]+) rows, ([\d.]+) ms/window = run ([\d.]+)"
    r" \(CUDA0 GPU-reach wait ([\d.]+) \+ CPU experts ([\d.]+)\) \+ commit ([\d.]+) \+ emit ([\d.]+)"
    r" \+ adapt wait ([\d.]+)"
    r".*routed entries per window: VRAM ([\d.]+) \(([\d.]+)%\), PCIe ([\d.]+) \(([\d.]+)%\),"
    r" CPU ([\d.]+) \(([\d.]+)%\)")
FIELDS = [(2, "ms_window"), (3, "run"), (4, "gpu_reach"), (5, "cpu_experts"), (6, "commit"),
          (7, "emit"), (8, "adapt_wait"), (10, "routed_VRAM_pct"), (12, "routed_PCIe_pct"),
          (14, "routed_CPU_pct")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eng", default="/data/champion/strata-nvfp4-engine.log")
    ap.add_argument("--boot", default="last", choices=["last", "all"])
    ap.add_argument("--min", type=int, default=1, help="drop buckets with fewer lines than this")
    a = ap.parse_args()
    data = open(a.eng, "rb").read().decode("utf-8", "replace")
    cuts = [m.start() for m in re.finditer(r"engine started:", data)]
    if a.boot == "last" and cuts:
        data = data[cuts[-1]:]
    rows = [[float(x) for x in m.groups()] for m in PAT.finditer(data)]
    print("boot=%s lines=%d" % (a.boot, len(rows)))
    if not rows:
        return
    buckets = {}
    for r in rows:
        buckets.setdefault(r[1], []).append(r)
    hdr = "avg_rows  n   " + "  ".join(nm for _, nm in FIELDS)
    print(hdr)
    for k in sorted(buckets):
        v = buckets[k]
        if len(v) < a.min:
            continue
        cells = "  ".join("%8s" % round(st.median([r[i] for r in v]), 2) for i, _ in FIELDS)
        print("%7.2f %4d  %s" % (k, len(v), cells))
    print("\ncolumns are ms/window medians within the bucket; routed_*_pct are the engine's own"
          " share of routed expert entries served from VRAM / PCIe / CPU per window.")


if __name__ == "__main__":
    main()

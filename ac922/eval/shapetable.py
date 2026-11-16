"""Turn note312/shapes.jsonl into the shape comparison table.

Rows are only trusted when every stream delivered the asked generation (dropped_streams 0);
a row with dropped streams is shown with a marker, because a short stream is an engine refusal,
not a measurement of speed.
"""
import argparse, collections, json

DEPTHS = [8000, 50000, 98000, 131000, 262000]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default="/data/work/note312/shapes.jsonl")
    ap.add_argument("--only", default="", help="comma list of tags to show")
    a = ap.parse_args()
    rows = []
    for ln in open(a.jsonl):
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if d.get("sum_tg") is None or "decode_live_s" not in d:
            continue
        if a.only and d["tag"] not in a.only.split(","):
            continue
        rows.append(d)
    by = collections.defaultdict(dict)
    tags = []
    for d in rows:
        if d["tag"] not in by:
            tags.append(d["tag"])
        key = (d["n"], d["depth_target"])
        cur = by[d["tag"]].get(key)
        if cur is None or (d.get("dropped_streams", 0) or 0) <= (cur.get("dropped_streams", 0) or 0):
            by[d["tag"]][key] = d
    hdr = "%-11s %-6s " % ("tag", "shape") + " ".join("%9s" % ("x%d@%dK" % (n, dd // 1000))
                                                       for n, dd in [(6, 8000), (6, 50000), (6, 98000), (6, 131000), (4, 262000)])
    hdr += "  %9s %9s %7s" % ("1@8K", "1@50K", "scale")
    print(hdr)
    print("-" * len(hdr))
    for t in tags:
        m = by[t]
        cells = []
        for n, dd in [(6, 8000), (6, 50000), (6, 98000), (6, 131000), (4, 262000)]:
            d = m.get((n, dd))
            v = "—"
            if d:
                v = "%.0f" % d["sum_tg"]
                if (d.get("dropped_streams") or 0) > 0:
                    v += "!"
            cells.append("%9s" % v)
        s1 = m.get((1, 8000), {}).get("sum_tg")
        s2 = m.get((1, 50000), {}).get("sum_tg")
        agg = m.get((6, 8000), {}).get("sum_tg")
        scale = "%.2f" % (agg / s1) if agg and s1 else "—"
        shape = (m.get((6, 8000), {}) or m.get((6, 50000), {})).get("shape") or ""
        short = shape.replace("pipelined, ", "").replace("groups of", "g/o")
        print("%-11s %-6s %s  %9s %9s %7s" % (t, short[-6:], " ".join(cells),
                                              "%.1f" % s1 if s1 else "—", "%.1f" % s2 if s2 else "—", scale))
    print("\n! = some streams came back short (engine refusal), the figure is not a speed measurement")
    print("scale = 6-stream aggregate / single-stream rate at 8K (published caliber: sum of per-stream tg)")


if __name__ == "__main__":
    main()

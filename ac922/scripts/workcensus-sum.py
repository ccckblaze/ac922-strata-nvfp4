#!/usr/bin/env python3.11
"""Summarize a workcensus CSV: busy %, util %, power, integrated joules and co-busy per phase.

Idle power is subtracted before integrating, so the energy column is work above the card's idle
floor.  Pair it against the wall time of the same phase: equal joules with equal wall time means the
arm moved load, not the bottleneck (DEVLOG H.33, lesson 24).

Usage: python3.11 ac922/scripts/workcensus-sum.py <nonce>   # env WCDIR, default /data/work
"""
import collections, os, statistics, sys, time
from datetime import datetime

nonce = sys.argv[1]
wcdir = os.environ.get("WCDIR", "/data/work")
rows = []
for ln in open(wcdir + "/workcensus-" + nonce + ".csv", errors="ignore"):
    f = [x.strip() for x in ln.split(",")]
    if len(f) < 5:
        continue
    try:
        ts = f[0].replace("/", "-")
        e = time.mktime(datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f").timetuple())
        e += int(ts.split(".")[1]) / 1000.0
        rows.append((e, int(f[1]), float(f[2]), float(f[3]), float(f[4])))
    except Exception:
        pass
phases = []
for ln in open(wcdir + "/workcensus-" + nonce + "-evt.txt", errors="ignore"):
    p = ln.rstrip("\n").split("\t")
    if len(p) >= 3:
        phases.append((p[0], float(p[1]), float(p[2]), p[3] if len(p) > 3 else ""))
cards = sorted(set(r[1] for r in rows))
iters = collections.defaultdict(dict)
for e, c, u, pw, ck in rows:
    k = round(e * 5) / 5
    iters[k][c] = (u, pw, ck)
idle = [pw for e, c, u, pw, ck in rows if e < phases[0][2]]
base = statistics.median(idle) if idle else 52.0
print("cards %s  samples %d  iters %d  idle-power %.1f W" % (cards, len(rows), len(iters), base))
print()
hdr = "%-12s %6s | " % ("phase", "n") + " ".join("c%d busy/util/pw/energy" % c for c in cards)
print(hdr)
print("-" * len(hdr))
for name, t0, t1, extra in phases:
    sub = [r for r in rows if t0 <= r[0] <= t1]
    per = collections.defaultdict(list)
    for e, c, u, pw, ck in sub:
        per[c].append((u, pw, ck))
    cells = []
    dt = 0.2
    for c in cards:
        v = per.get(c, [])
        if not v:
            cells.append("%22s" % "-"); continue
        busy = 100.0 * sum(1 for x in v if x[0] > 0) / len(v)
        um = statistics.mean(x[0] for x in v)
        pm = statistics.median(x[1] for x in v)
        en = sum(max(0.0, x[1] - base) * dt for x in v)
        cells.append("%5.1f%% %4.0f%% %4.0fW %5.0fJ" % (busy, um, pm, en))
    print("%-12s %6d | " % (name, len(sub)) + " | ".join(cells))
    if extra:
        print("%19s%s" % ("", extra))
print()
print("co-busy (cards with util>0 in the same 200ms tick)")
for name, t0, t1, extra in phases:
    h = collections.Counter()
    for k, d in iters.items():
        if not (t0 <= k <= t1):
            continue
        h[sum(1 for c in cards if c in d and d[c][0] > 0)] += 1
    tot = sum(h.values()) or 1
    print("  %-12s " % name + "  ".join("%d-card %4.1f%%" % (i, 100.0 * h.get(i, 0) / tot)
                                        for i in range(len(cards) + 1)))

#!/usr/bin/env python3
"""Decode-timing sentinel: parse the engine's `strata decode timing:` lines into rows and report
median/p90 per component.  Incremental: a watermark file remembers the engine-log byte offset, so
each run reads only new bytes.  Numbers only - never any model text.

  decodetiming_watch.py            scan new lines, append rows, print nothing on success
  decodetiming_watch.py --report   print the last-24h distribution (also usable standalone)

Rows land in /data/work/decodetiming/rows-YYYYMM.jsonl; the watermark is /data/work/decodetiming.pos
"""
import json, os, re, statistics, sys, time

ENG = os.environ.get("ENGINE_LOG", "/data/champion/strata-nvfp4-engine.log")
DIR = os.environ.get("DT_DIR", "/data/work/decodetiming")
POS = os.path.join(DIR, "watch.pos")
NUM = r"(-?\d+(?:\.\d+)?)"
LINE_RE = re.compile(
    r"strata decode timing: (?P<windows>\d+) windows, avg T " + NUM + r", (?P<tpw>" + NUM +
    r") tokens/window, (?P<mswin>" + NUM + r") ms/window = adapt wait (?P<adapt_wait>" + NUM +
    r") \+ verify (?P<verify>" + NUM + r") \(GPU-reach wait (?P<gpu_reach>" + NUM +
    r") \+ per-layer host (?P<host>" + NUM + r") \[plan (?P<plan>" + NUM + r") actq (?P<actq>" +
    NUM + r") jobs (?P<jobs>" + NUM + r") CPU (?P<cpu>" + NUM + r")\] \+ stage (?P<stage>" + NUM +
    r")\) \+ commit/emit (?P<commit>" + NUM + r") \+ draft (?P<draft>" + NUM + r") \+ adapt join " +
    r"(?P<adapt_join>" + NUM + r")")

def scan():
    pos = 0
    if os.path.exists(POS):
        pos = int(open(POS).read().strip())
    size = os.path.getsize(ENG)
    if size < pos:                      # log rotated or truncated: rescan from 0
        pos = 0
    rows = []
    with open(ENG, "rb") as f:
        f.seek(pos)
        for raw in f:
            t = raw.decode("utf-8", "replace")
            m = LINE_RE.search(t)
            if not m:
                continue
            r = {k: (int(v) if k == "windows" else float(v)) for k, v in m.groupdict().items()}
            r["tg"] = round(r["tpw"] / r["mswin"] * 1000.0, 2) if r["mswin"] > 0 else None
            r["ts"] = int(time.time())
            rows.append(r)
        newpos = f.tell()
    if rows:
        os.makedirs(DIR, exist_ok=True)
        with open(os.path.join(DIR, "rows-%s.jsonl" % time.strftime("%Y%m")), "a") as o:
            for r in rows:
                o.write(json.dumps(r) + "\n")
    with open(POS, "w") as o:
        o.write(str(newpos))
    return len(rows)

def report():
    cutoff = int(time.time()) - 86400
    rows = []
    for name in sorted(os.listdir(DIR)) if os.path.isdir(DIR) else []:
        if not name.startswith("rows-"):
            continue
        for line in open(os.path.join(DIR, name)):
            r = json.loads(line)
            if r["ts"] >= cutoff:
                rows.append(r)
    if not rows:
        print("DT-REPORT: no rows in the last 24 h"); return
    keys = ["mswin", "tg", "tpw", "verify", "gpu_reach", "host", "cpu", "draft", "commit",
            "adapt_wait", "adapt_join", "plan", "actq"]
    print("DT-REPORT  n=%d lines, last 24 h" % len(rows))
    print("%-12s %8s %8s %8s" % ("field", "median", "p90", "min"))
    for k in keys:
        v = sorted(r[k] for r in rows if r.get(k) is not None)
        if not v:
            continue
        med = statistics.median(v)
        p90 = v[min(len(v) - 1, int(len(v) * 0.9))]
        print("%-12s %8.2f %8.2f %8.2f" % (k, med, p90, v[0]))
    wins = sum(r["windows"] for r in rows)
    print("window coverage: %d engine windows in %d request lines" % (wins, len(rows)))

if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
    else:
        n = scan()
        if n and os.environ.get("DT_QUIET") != "1":
            print("DT-SCAN appended %d rows" % n)

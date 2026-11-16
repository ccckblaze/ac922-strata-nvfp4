#!/usr/bin/env python3
"""Cross-line fidelity table with caliber overrides (runbook 101.3 / 101.5).

Numbers only. Differences from /data/work/evalreport.py:
  * N1's ceval/cmmlu smoke cells come from eval-nvfp4-mcfix.log when present -- the original smoke
    ran the MC tasks with a 400-token thinking budget, which measured the budget, not the model.
  * N1 smoke cmmlu is suppressed to N/A: that recheck ran before the cmmlu answer keys were
    rebuilt (both jsonl splits had empty question+answer), so no score from it is meaningful.
  * mean_s is averaged over scored samples only, and errors are surfaced per task.
"""
import os, re, sys

SUM = re.compile(r"SUMMARY task=(\S+) n=(\d+) errors=(\d+) scored=(\d+) hits=(\d+) acc=([\d.]+)% tok_s=([\d.]+) mean_s=([\d.]+)(.*)")
ORDER = ["gsm8k", "math", "aime", "humaneval", "humaneval_plus", "mbpp", "mbpp_plus",
         "ifeval", "ceval", "cmmlu", "lcb"]
LANES = [("N1 NVFP4", "eval-nvfp4-smoke.log"), ("S1 int8", "eval-s1-smoke.log"),
         ("M1 Monster", "eval-mon-smoke.log"), ("N1 FULL", "eval-nvfp4-full.log")]
# (lane, task) -> log whose SUMMARY wins
PREFER = {("N1 NVFP4", "ceval"): "eval-nvfp4-mcfix.log",
          ("N1 NVFP4", "cmmlu"): "eval-nvfp4-mcfix.log"}
# (lane, task) -> forced N/A with reason
NA = {("N1 NVFP4", "cmmlu"): "keys-fixed-later"}
W = sys.argv[1] if len(sys.argv) > 1 else "/data/work"


def parse(path):
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8", errors="replace"):
        m = SUM.search(line)
        if m:
            task, n, err, scored, hits, acc, toks, ms, tail = m.groups()
            out[task] = (int(n), int(err), int(scored), int(hits), float(acc),
                         float(toks), float(ms), tail.strip())
    return out


logs = {}
for _, f in LANES:
    logs[f] = parse(os.path.join(W, f))
for f in set(PREFER.values()):
    logs.setdefault(f, parse(os.path.join(W, f)))

print("| task | " + " | ".join(nm for nm, _ in LANES) + " |")
print("|---" * (len(LANES) + 1) + "|")
for t in ORDER:
    cells = []
    for nm, base in LANES:
        f = PREFER.get((nm, t), base)
        r = logs[f].get(t)
        if (nm, t) in NA:
            cells.append("N/A (%s)" % NA[(nm, t)])
        elif not r:
            cells.append("\u2014")
        else:
            cell = "%s%% (%d/%d)" % ("%.1f" % r[4], r[3], r[0])
            if r[1]:
                cell += " err=%d" % r[1]
            if f != base:
                cell += " [mc]"
            cells.append(cell)
    print("| %s | %s |" % (t, " | ".join(cells)))

print()
for nm, base in LANES:
    rs = logs[base]
    if not rs:
        print("%s: (no results yet)" % nm)
        continue
    sc = sum(r[2] for r in rs.values())
    hit = sum(r[3] for r in rs.values())
    er = sum(r[1] for r in rs.values())
    tk = [r[5] for r in rs.values() if r[5] > 0]
    print("%s: tasks=%d scored=%d hits=%d errors=%d  mean_tok_s=%.1f" % (
        nm, len(rs), sc, hit, er, sum(tk) / len(tk) if tk else 0.0))
    if os.path.exists(os.path.join(W, base)):
        tail = open(os.path.join(W, base), encoding="utf-8", errors="replace").read()
        print("   suite_done=%s needle_rows=%d" % ("SUITE-DONE" in tail, tail.count("NEEDLE")))

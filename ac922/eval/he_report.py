#!/usr/bin/env python3
"""Cross-lane HumanEval comparison. Numbers only (runbook 101.7b).

Reads per-lane per-sample JSONL written by eval_runner and reports per-lane accuracy plus the
agreement matrix. The matrix is the point: two lanes at similar accuracy can disagree on which
tasks they fail, and "only A passes" vs "both fail" separates engine-path damage from weight damage.
"""
import json, os, sys

LANES = [("N1 NVFP4", "/root/ac922-eval/out/he-n1/humaneval.jsonl"),
         ("S1 int8", "/root/ac922-eval/out/he-s1/humaneval.jsonl"),
         ("M1 Monster", "/root/ac922-eval/out/he-m1/humaneval.jsonl")]
want = sys.argv[1] if len(sys.argv) > 1 else ""
data = {}
for name, path in LANES:
    if want and want not in name:
        continue
    if not os.path.exists(path):
        print("%-11s (no file yet: %s)" % (name, path))
        continue
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    sc = [r for r in rows if not r.get("error")]
    ct = [r.get("ctok") or 0 for r in sc]
    data[name] = {r.get("key"): bool(r.get("hit")) for r in sc}
    empty = [r for r in sc if not (r.get("text") or "").strip()]
    print("%-11s n=%3d acc=%6.2f%% errors=%d empty_text=%d ctok_mean=%6.1f ctok_max=%d" % (
        name, len(sc), 100.0 * sum(1 for v in data[name].values() if v) / max(1, len(sc)),
        len(rows) - len(sc), len(empty), sum(ct) / max(1, len(ct)), max(ct) if ct else 0))

if len(data) >= 2:
    keys = set()
    for d in data.values():
        keys |= set(d)
    common = [k for k in keys if all(k in d for d in data.values())]
    print("\ncommon samples=%d" % len(common))
    names = list(data)
    if len(names) == 2:
        a, b = names
        both = sum(1 for k in common if data[a][k] and data[b][k])
        onlya = sum(1 for k in common if data[a][k] and not data[b][k])
        onlyb = sum(1 for k in common if not data[a][k] and data[b][k])
        none = sum(1 for k in common if not data[a][k] and not data[b][k])
        print("%s+%s=%d  only_%s=%d  only_%s=%d  both_fail=%d" % (a, b, both, a, onlya, b, onlyb, none))
        dis = both + onlya + onlyb + none
        print("disagreement=%d (%.1f%%)" % (onlya + onlyb, 100.0 * (onlya + onlyb) / max(1, dis)))
    else:
        for k in common:
            pass
        allp = sum(1 for k in common if all(d[k] for d in data.values()))
        allf = sum(1 for k in common if not any(d[k] for d in data.values()))
        print("all_pass=%d all_fail=%d mixed=%d" % (
            allp, allf, len(common) - allp - allf))

#!/usr/bin/env python3
"""Offline ledger comparison: quant-vs-reference agreement, flips and regression cases.
Zero GPU/HTTP. Usage: python3.11 compare_results.py A.jsonl B.jsonl [--label-a q4 --label-b fp16]
A and B are eval_runner per-sample ledgers covering the same task/key pairs.
"""
import argparse, collections, json, sys


def load(p):
    d = {}
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("error"):
            r["hit"] = None
        d[(r.get("task"), r.get("key"))] = r
    return d


def same(a, b):
    if a is None or b is None:
        return False
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().upper() == b.strip().upper()
    return str(a).strip() == str(b).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a"); ap.add_argument("b")
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--dump-regressions", default="")
    a = ap.parse_args()
    A, B = load(a.a), load(a.b)
    common = sorted(set(A) & set(B))
    print("PAIRS a=%d b=%d common=%d" % (len(A), len(B), len(common)))
    if not common:
        sys.exit("NO OVERLAP: nothing to compare")
    stats = collections.Counter()
    pred_flip = collections.Counter()
    reg = []
    for k in common:
        ra, rb = A[k], B[k]
        ha, hb = ra.get("hit"), rb.get("hit")
        stats["n"] += 1
        pa, pb = ra.get("pred"), rb.get("pred")
        if pa is not None or pb is not None:          # code tasks carry no extracted pred -> skip
            stats["pred_cmp"] += 1
            if same(pa, pb):
                stats["pred_agree"] += 1
            else:
                pred_flip[ra.get("task")] += 1
        if ha is None or hb is None:
            stats["skip_error"] += 1
            continue
        if ha and hb:
            stats["both_correct"] += 1
        elif ha and not hb:
            stats["regression"] += 1
            reg.append(k)
        elif hb and not ha:
            stats["recovery"] += 1
        else:
            stats["both_wrong"] += 1
    n = max(1, stats["both_correct"] + stats["regression"] + stats["recovery"] + stats["both_wrong"])
    pc = max(1, stats["pred_cmp"])
    print("AGREEMENT pred_identical=%.2f%% (%d/%d scored-pairs)" % (
        100.0 * stats["pred_agree"] / pc, stats["pred_agree"], stats["pred_cmp"]))
    print("HIT matrix: both_correct=%d regression(%s_only=%d) recovery(%s_only=%d) both_wrong=%d skip_error=%d" % (
        stats["both_correct"], a.label_a, stats["regression"], a.label_b, stats["recovery"],
        stats["both_wrong"], stats["skip_error"]))
    acc_a = 100.0 * (stats["both_correct"] + stats["regression"]) / n
    acc_b = 100.0 * (stats["both_correct"] + stats["recovery"]) / n
    print("ACC %s=%.2f%% %s=%.2f%% delta=%+.2fpp net_regression=%.2fpp" % (
        a.label_a, acc_a, a.label_b, acc_b, acc_a - acc_b, 100.0 * stats["regression"] / n))
    if pred_flip:
        print("PRED_FLIPS_by_task " + " ".join("%s=%d" % (t, c) for t, c in pred_flip.most_common()))
    if reg and a.dump_regressions:
        with open(a.dump_regressions, "w", encoding="utf-8") as fh:
            for k in reg:
                fh.write(json.dumps({"task": k[0], "key": k[1], "gold": A[k].get("gold"),
                                     "pred_a": A[k].get("pred"), "pred_b": B[k].get("pred")},
                                    ensure_ascii=False) + "\n")
        print("REGRESSIONS_DUMPED %s (%d rows)" % (a.dump_regressions, len(reg)))


main()

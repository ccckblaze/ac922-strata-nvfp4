#!/usr/bin/env python3
"""Budget-cap tail repair for the AC922 fidelity campaign (runbook 101.6b).

A sample whose completion tokens hit the task's max-tok cap is graded by the budget, not by the
model. This tool finds those samples in an existing per-sample JSONL dir, re-asks them with a
larger budget through the same runner (same prompt, same scorer, same thinking), and reports the
score delta as counts only.

Modes:
  --regrade-only : no requests. Re-score the stored text with this tool's scorers and diff against
                   the recorded hit. Proves the two grading paths agree (CPU-only acceptance test).
  --fix          : re-ask capped-and-wrong samples at --factor x budget, write tailfix_<task>.jsonl.
stdout is count-only by discipline: never model text.
"""
import argparse, json, os, sys, time

EVALD = os.environ.get("SB_EVALD", "/root/ac922-eval")
sys.path.insert(0, EVALD)

# task (jsonl basename) -> (runner max-tok in full, internal tok_floor, thinking)
FULL = {
    "gsm8k":         (4000, 0,    1),
    "math":          (8000, 4000, 1),
    "aime":          (16000, 8000, 1),
    "humaneval":     (3000, 2000, 1),
    "humaneval_plus": (3000, 2000, 1),
    "mbpp":          (3000, 2000, 1),
    "mbpp_plus":     (3000, 2000, 1),
    "ifeval":        (3000, 1500, 1),
    "ceval":         (1200, 0,    0),
    "cmmlu":         (1200, 0,    0),
    "lcb":           (3000, 2500, 1),
}
SMOKE = {
    "gsm8k": (2000, 0, 1), "math": (6000, 4000, 1), "aime": (12000, 8000, 1),
    "humaneval": (2000, 2000, 1), "humaneval_plus": (2000, 2000, 1),
    "mbpp": (2000, 2000, 1), "mbpp_plus": (2000, 2000, 1),
    "ifeval": (2000, 1500, 1), "ceval": (400, 0, 1), "cmmlu": (400, 0, 1),
    "lcb": (3000, 2500, 1),
}


def rows_of(path):
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def grade(R, task, rec, text):
    """Same scoring path as eval_runner, given a completion string."""
    if task == "gsm8k":
        p = R.last_int(text)
        gold = rec.get("gold")
        return p, (p is not None and p == gold)
    if task in ("math", "aime"):
        return R._grade_math(text, rec.get("gold"))
    if task in ("ceval", "cmmlu"):
        p = R.pred_letter(text)
        return p, (p == (rec.get("gold") or "").strip().upper())
    if task == "ifeval":
        kws = rec.get("kw")
        if kws is None:
            ex = R.load("ifeval.jsonl")[rec["idx"]]
            kws = [{"name": n, "kwargs": k} for n, k in
                   zip(ex["instruction_id_list"], ex["kwargs"])]
        return None, bool(R.ifeval_check(text, kws))
    if task in ("humaneval", "humaneval_plus", "mbpp", "mbpp_plus"):
        mbpp = task.startswith("mbpp"); plus = task.endswith("_plus")
        ex = R.load(R.CODE_DATA[task])[rec["idx"]]
        body = R.strip_fence(text)
        prog, tmo = R.code_program(ex, body, mbpp, plus)
        return None, bool(R.code_exec(prog, tmo))
    if task == "lcb":
        want = rec.get("row") or rec.get("key")
        ex = None
        for cand in R.load("lcb_v56.jsonl"):
            if cand.get("key") == want:
                ex = cand; break
        if ex is None:
            raise SystemExit("lcb row not found: %r" % (want,))
        hit, cnt, det = __import__("lcb_judge").grade(ex, R.strip_fence(text))
        return None, bool(hit)
    raise SystemExit("unknown task %r" % (task,))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="out/<ts>_<mode> dir")
    ap.add_argument("--caliber", choices=["full", "smoke"], default="full")
    ap.add_argument("--task", default="", help="restrict to one jsonl basename")
    ap.add_argument("--factor", type=float, default=2.0)
    ap.add_argument("--regrade-only", action="store_true")
    ap.add_argument("--max-repair", type=int, default=10 ** 9)
    a = ap.parse_args()

    os.environ.setdefault("SB_URL", "http://127.0.0.1:8091")
    import eval_runner as R

    table = FULL if a.caliber == "full" else SMOKE
    files = sorted(f for f in os.listdir(a.dir) if f.endswith(".jsonl"))
    grand = {"n": 0, "capped": 0, "capped_wrong": 0, "repaired": 0}
    for fn in files:
        base = fn[:-6]
        # Only the recheck suffix is stripped: humaneval_plus / mbpp_plus are tasks of their own,
        # and folding them here grades them with the wrong scorer (seen as mismatch=13).
        stem = base[:-6] if base.endswith("_mcfix") else base
        if stem not in table:
            continue
        if a.task and stem != a.task:
            continue
        bud, floor, think = table[stem]
        eff = max(floor, bud)
        recs = rows_of(os.path.join(a.dir, fn))
        if not recs:
            continue
        scored = [r for r in recs if not r.get("error")]
        n_hit = sum(1 for r in scored if r.get("hit"))
        capped = [r for r in scored if (r.get("ctok") or 0) >= eff - 1]
        wrong = [r for r in capped if not r.get("hit")]

        if a.regrade_only:
            mismatch = 0
            checked = 0
            for r in scored:
                t = r.get("text")
                if t is None:
                    continue
                checked += 1
                try:
                    _, h = grade(R, stem, r, t)
                except SystemExit:
                    raise
                if bool(h) != bool(r.get("hit")):
                    mismatch += 1
            print("REGRADE task=%-15s n=%d checked=%d mismatch=%d" % (stem, len(recs), checked, mismatch))
            continue

        print("TASK task=%-15s n=%d acc=%.2f%% eff_budget=%d capped=%d capped_wrong=%d" % (
            stem, len(scored), 100.0 * n_hit / max(1, len(scored)), eff, len(capped), len(wrong)))
        grand["n"] += len(scored); grand["capped"] += len(capped)
        grand["capped_wrong"] += len(wrong)
        if not wrong:
            continue
        outp = os.path.join(a.dir, "tailfix_%s.jsonl" % stem)
        fixed = 0
        with open(outp, "w", encoding="utf-8") as fh:
            for r in wrong[: a.max_repair]:
                newbud = int(eff * a.factor)
                try:
                    text, ctok, el = R.ask(r["prompt"], think, newbud)
                    p, h = grade(R, stem, r, text)
                except Exception as e:
                    fh.write(json.dumps({"key": r.get("key"), "error": str(e)[:200]}) + "\n"); fh.flush()
                    continue
                fh.write(json.dumps({"key": r.get("key"), "idx": r.get("idx"), "budget": newbud,
                                     "ctok": ctok, "elapsed": round(el, 2), "hit": bool(h),
                                     "still_capped": ctok >= newbud - 1}) + "\n")
                fh.flush()
                if h:
                    fixed += 1
        grand["repaired"] += fixed
        print("  REPAIR task=%-15s tried=%d flipped_to_hit=%d out=%s" % (
            stem, min(len(wrong), a.max_repair), fixed, outp))

    if not a.regrade_only:
        print("GRAND n=%d capped=%d capped_wrong=%d repaired=%d" % (
            grand["n"], grand["capped"], grand["capped_wrong"], grand["repaired"]))


if __name__ == "__main__":
    main()

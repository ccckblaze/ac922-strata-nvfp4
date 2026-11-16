#!/usr/bin/env python3
"""Offline readiness gate for the AC922 eval env. Zero HTTP, zero GPU.
Checks dataset wiring, required fields, prompt assembly, and answer extractors. Prints PASS/FAIL only.
"""
import json, os, sys

sys.path.insert(0, "/root/ac922-eval")
import eval_runner as R

DATA = R.DATA
FAIL = []


def chk(cond, label, detail=""):
    print(("PASS " if cond else "FAIL ") + label + (" " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


EXPECT = {
    "gsm8k_test.jsonl": (1319, ["question", "answer"]),
    "gsm8k_train.jsonl": (7473, ["question", "answer"]),
    "math500.jsonl": (500, ["problem", "answer", "unique_id"]),
    "aime2024.jsonl": (30, ["problem", "answer"]),
    "aime2025.jsonl": (30, ["question", "answer"]),
    "ifeval.jsonl": (541, ["prompt", "instruction_id_list", "kwargs"]),
    "ifbench.jsonl": (300, ["prompt", "instruction_id_list", "kwargs"]),
    "gpqa_diamond.jsonl": (198, ["subject", "question", "choices", "answer"]),
    "HumanEval.jsonl.gz": (164, ["prompt", "canonical_solution", "test", "entry_point"]),
    "humaneval_plus.jsonl": (164, ["prompt", "test", "entry_point"]),
    "mbpp.jsonl": (974, ["text", "test_list"]),
    "mbpp_plus.jsonl": (378, ["prompt", "code", "test"]),
    "cmmlu_test.jsonl": (11582, ["subject", "question", "choices", "answer"]),
    "cmmlu_dev.jsonl": (335, ["subject", "question", "choices", "answer"]),
    # official C-Eval split sizes: dev 260 / val 1346 / test 12343 (val is the scoreable set)
    "ceval_val.jsonl": (1346, ["subject", "question", "A", "B", "C", "D", "answer"]),
    "ceval_dev.jsonl": (260, ["subject", "question", "A", "B", "C", "D", "answer"]),
}

for fn, (nmin, keys) in EXPECT.items():
    p = os.path.join(DATA, fn)
    if not os.path.exists(p):
        chk(False, "exists:" + fn, "missing")
        continue
    rows = R.load(fn)
    chk(len(rows) >= nmin, "count:" + fn, "got %d want>=%d" % (len(rows), nmin))
    missing = [k for k in keys if k not in rows[0]]
    chk(not missing, "fields:" + fn, str(missing))
    # BUG FIX 2026-11-16: the old default was r.get("answer", "x"), which turns an empty string
    # into "x" and therefore PASSES a file whose answer column is 100% blank. That is exactly how
    # cmmlu slipped through the gate with 11,582/11,582 empty answers (runbook 101.4).
    # Only files that carry these fields are held to them: code/instruction tasks legitimately
    # have no "answer" column (they grade by exec / rule checkers).
    if "answer" in rows[0]:
        gold = [r for r in rows if not str(r.get("answer", "") or "").strip()]
        chk(len(gold) == 0, "gold-nonempty:" + fn, "%d empty" % len(gold))
    qk = [k for k in ("question", "problem", "text", "prompt", "content") if k in rows[0]]
    if qk:
        q = [r for r in rows if not any(str(r.get(k, "") or "").strip() for k in qk)]
        chk(len(q) == 0, "question-nonempty:" + fn + ":" + ",".join(qk), "%d empty" % len(q))
    if "choices" in rows[0]:
        bad = [r for r in rows if not all(str((r.get("choices") or {}).get(k, "") or "").strip()
                                         for k in "ABCD")]
        chk(len(bad) == 0, "choices-nonempty:" + fn, "%d empty" % len(bad))

# extractors
cases_letter = [("Answer: B", "B"), ("答案是 C", "C"), ("The correct answer is D.", "D"),
                ("\\boxed{A}", "A"), ("...最终答案：\n\nAnswer: a", "A"), ("no letter here", None)]
for txt, want in cases_letter:
    chk(R.pred_letter(txt) == want, "pred_letter", "%r -> %r want %r" % (txt[:20], R.pred_letter(txt), want))
cases_int = [("\\boxed{42}", 42), ("#### 5", 5), ("no digits", None)]
for txt, want in cases_int:
    chk(R.pred_int(txt) == want, "pred_int", "%r -> %r want %r" % (txt[:20], R.pred_int(txt), want))
for txt, gold, want in [("The answer is 1,234.", "1234", True), ("\\boxed{1,234}", "1234", True),
                        ("\\boxed{\\frac{1}{2}}", "1/2", True), ("\\boxed{0.5}", "1/2", True),
                        ("result: 3.14", "3.14", True), ("\\boxed{7}", "8", False),
                        ("The answer is $277$", "277", True)]:
    p_, hit = R._grade_math(txt, gold)
    chk(hit == want, "grade_math", "%r/%r -> %r %r want %r" % (txt[:22], gold, p_, hit, want))
chk(R.last_int("42") == 42 and R.strip_fence("```python\nx=1\n```").strip() == "x=1", "helpers")

# MC prompt assembly (5-shot)
dev = {}
for d in R.load("ceval_dev.jsonl"):
    dev.setdefault(d["subject"], []).append(d)
row = R.load("ceval_val.jsonl")[0]
shots = "\n\n".join(R.mc_block_ceval(d) + "\nAnswer: " + d["answer"].strip().upper() for d in dev[row["subject"]][:5])
prompt = shots + "\n\n" + R.mc_block_ceval(row) + R.MC_SUFFIX
nshot = prompt.count("\nAnswer: ")
chk(nshot == 5 and prompt.endswith("Answer: X") and prompt.count(". ") >= 8 and len(prompt) > 200,
    "ceval-prompt", "shots=%d len=%d" % (nshot, len(prompt)))
mrow = R.load("cmmlu_test.jsonl")[0]
mdev = {}
for d in R.load("cmmlu_dev.jsonl"):
    mdev.setdefault(d["subject"], []).append(d)
mp = "\n\n".join(R.mc_block_cmmlu(d) + "\nAnswer: " + d["answer"].strip().upper() for d in mdev[mrow["subject"]][:5]) + R.MC_SUFFIX
chk(mp.count("\nAnswer: ") == 5 and mp.endswith("Answer: X"), "cmmlu-prompt", "shots=%d" % mp.count("\nAnswer: "))
subj_c = len({r["subject"] for r in R.load("ceval_val.jsonl")})
subj_m = len({r["subject"] for r in R.load("cmmlu_test.jsonl")})
chk(subj_c == 52, "ceval-subjects", str(subj_c))
chk(subj_m == 67, "cmmlu-subjects", str(subj_m))

# GPQA Diamond: 198 four-option items, letters balanced (shuffle is seeded per Question ID),
# English prompt assembly, single-letter answer protocol.
gq = R.load("gpqa_diamond.jsonl")
letters = {}
for r in gq:
    letters[r["answer"]] = letters.get(r["answer"], 0) + 1
chk(len(gq) == 198, "gpqa-count", str(len(gq)))
chk(min(letters.values()) >= 40, "gpqa-letter-balance", str(letters))
chk(len({r["subject"] for r in gq}) == 3, "gpqa-subjects", str(sorted({r["subject"] for r in gq})))
gp = R.mc_block_en(gq[0]) + R.MC_SUFFIX_EN
chk(gp.endswith("Answer: X") and len(gp) > 120, "gpqa-prompt", "len=%d" % len(gp))

# IFBench: every instruction id must resolve in the vendored official registry, otherwise the
# score silently ignores constraints (the same trap that made ifeval_partial optimistic).
import ifbench_grade  # noqa: E402
ib = R.load("ifbench.jsonl")
unresolved = {i for r in ib for i in r["instruction_id_list"] if i in ifbench_grade.resolve(r["instruction_id_list"])}
chk(not unresolved, "ifbench-ids-resolvable", "missing=%d %s" % (len(unresolved), sorted(unresolved)[:4]))
chk(len({i for r in ib for i in r["instruction_id_list"]}) == 58, "ifbench-id-count",
    str(len({i for r in ib for i in r["instruction_id_list"]})))
ie_unres = sum(1 for r in R.load("ifeval.jsonl") if ifbench_grade.resolve(r["instruction_id_list"]))
chk(ie_unres == 0, "ifeval-coverage", "rows_with_unresolved_ids=%d" % ie_unres)

# ifeval verdict sanity, through the single official scorer (runner == ref_run path)
for txt, kw, want, label in [
        ("hello world", [{"name": "keywords:existence", "kwargs": {"keywords": ["world"]}}], True, "kw-pos"),
        ("hello world", [{"name": "keywords:existence", "kwargs": {"keywords": ["nope"]}}], False, "kw-neg"),
        ('{"a": 1}', [{"name": "detectable_format:json_format", "kwargs": {}}], True, "json-pos"),
        ("a,b", [{"name": "punctuation:no_comma", "kwargs": {}}], False, "comma-neg")]:
    hit, _x, err = R.rule_grade("p", txt, kw)
    chk(hit is want and err is None, "ifeval-" + label, "got=%r err=%r" % (hit, err))
hit, extras, err = R.rule_grade("p", "x", [{"name": "no.such:checker", "kwargs": {}}])
chk(err is not None and hit is False, "ifeval-unresolved-is-error", str(err))

# code harness sanity: a trivially wrong program must fail
chk(R.code_exec("raise SystemExit(1)\nprint('PASSED')", 10) is False, "code_exec-negative")
chk(R.code_exec("print('PASSED')", 10) is True, "code_exec-positive")

# results dir writable
os.makedirs(os.path.join(DATA, "results"), exist_ok=True)
chk(os.access(os.path.join(DATA, "results"), os.W_OK), "results-writable")

# LiveCodeBench gate (streamed; lcb_v56.jsonl is ~1.4 GB and must not be list-loaded here)
import itertools, json as _json
lp = os.path.join(DATA, "lcb_v56.jsonl")
if os.path.exists(lp):
    n = sum(1 for _ in open(lp, encoding="utf-8"))
    chk(n >= 342, "count:lcb_v56.jsonl", "got %d want>=342" % n)
    bad = 0
    with open(lp, encoding="utf-8") as fh:
        for row in itertools.islice(((_json.loads(l) for l in fh)), 0, 342):
            t = row.get("tests") or []
            if (row.get("platform") not in ("atcoder", "leetcode") or not t
                    or not all(isinstance(c, list) and len(c) == 2 and all(isinstance(x, str) for x in c) for c in t)
                    or row.get("tests_src") != "private" or not (row.get("content") or "").strip()):
                bad += 1
    chk(bad == 0, "lcb-structure", "bad=%d/342" % bad)
    import lcb_judge
    ex = {"key": "gate:stdin", "platform": "atcoder", "tests": [["3 5", "8"]]}
    h1, c1, _ = lcb_judge.grade(ex, "a,b=map(int,input().split());print(a+b)")
    h2, c2, _ = lcb_judge.grade(ex, "print(9)")
    chk(h1 is True and h2 is False, "lcb-judge-gate", "%s %s" % (c1, c2))
    lex = {"key": "gate:lc", "platform": "leetcode", "class": "Solution", "method": "double",
           "tests": [["5", "10"]]}
    h3, _, _ = lcb_judge.grade(lex, "class Solution:\n    def double(self, x):\n        return x * 2")
    h4, _, _ = lcb_judge.grade(lex, "class Solution:\n    def double(self, x):\n        return x + 2")
    chk(h3 is True and h4 is False, "lcb-judge-gate-lc", "%s %s" % (h3, h4))
else:
    chk(False, "exists:lcb_v56.jsonl", "missing")

print("SELFCHECK %s failures=%d" % ("OK" if not FAIL else "FAILED", len(FAIL)))
sys.exit(0 if not FAIL else 1)

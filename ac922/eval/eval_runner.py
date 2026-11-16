#!/usr/bin/env python3
"""Torch-free eval runner -> /v1/chat/completions (env SB_URL, default http://127.0.0.1:8091).

Tasks: gsm8k math aime humaneval humaneval_plus mbpp mbpp_plus ifeval ceval cmmlu lcb
Usage: python3.11 eval_runner.py <task> [--limit N] [--thinking 0|1] [--max-tok T]
                                [--shot K] [--subject S] [--out FILE] [--tag T] [--selftest]
Scoring is pure stdlib; code tasks exec in a tmp dir with a hard timeout. The runner only speaks
HTTP to the engine and never touches the GPU. --selftest (code tasks only) runs the canonical
solution through the same exec harness with zero requests, proving dataset+harness wiring.
For lcb, --selftest is a wiring smoke (fixed stub submission must grade without SYS errors);
grader semantics are covered separately by lcb_judge.py --selftest.
Per-sample records go to --out as JSONL so agreement-rate and regression-case analysis against a
reference run can be done offline; stdout prints keys and counts only.
"""
import argparse, gzip, json, os, re, shutil, subprocess, sys, tempfile, time
import urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lcb_judge

URL = os.environ.get("SB_URL", "http://127.0.0.1:8091")
DATA = os.environ.get("SB_DATA", "/root/ac922-eval/data")
MODEL = os.environ.get("SB_MODEL", "qwen3.8-flash-next")
# SB_ENDPOINT: "chat" (default, OpenAI-compatible /v1/chat/completions) or "completion"
# (llama.cpp raw /completion, no chat template). The IQ3_XXS monster pack's chat endpoint is
# unusable (runbook 73.1: template-wrapped requests stop at 1 token with empty content), so the
# monster line is graded through the same prompts + same scorers over the completion endpoint.
ENDPOINT = os.environ.get("SB_ENDPOINT", "chat")
BIG = 10 ** 9
# SB_STOP: stop strings for the raw /completion endpoint only, "|"-separated, backslash escapes
# honoured ("\ndef " -> newline + "def "). The monster pack has no chat template, so nothing tells
# it where the completion ends and it happily runs past the function it was asked to write; the
# fenced-block scorer survives that but a plain continuation does not. Chat lanes are unaffected.
STOP = [s.encode("utf-8").decode("unicode_escape")
        for s in os.environ.get("SB_STOP", "").split("|") if s]
NUM = re.compile(r"(?<![\w])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
BOX = re.compile(r"\\boxed\{([^{}]*)\}")
def extract_boxed(text):
    """Last \\boxed{...} content with brace-depth matching, so \\boxed{\\frac{1}{2}} survives."""
    t = text or ""
    out = None
    i = 0
    key = "\\boxed{"
    while True:
        j = t.find(key, i)
        if j < 0:
            return out
        k = start = j + len(key)
        depth = 1
        while k < len(t) and depth:
            if t[k] == "{":
                depth += 1
            elif t[k] == "}":
                depth -= 1
            k += 1
        out = t[start:k - 1]
        i = j + 8


ANS = re.compile(r"(?:ANSWER|Answer|answer|最终答案|答案)\s*(?:是|为|is|：|:)\s*\**\s*([A-Da-d])(?![A-Za-z])")
LET = re.compile(r"\b([A-Da-d])\b")
MATH_SUFFIX = "\n\nSolve step by step and end with \\boxed{your final answer}."
MC_SUFFIX = "\n\n\u8bf7\u53ea\u8f93\u51fa\u4e00\u4e2a\u9009\u9879\u5b57\u6bcd\uff0c\u6700\u540e\u4e00\u884c\u683c\u5f0f\u4e3a\uff1aAnswer: X"
CODE_SYS = {"humaneval": "Complete the function to pass hidden tests. Return ONLY python code, no prose.\n",
            "mbpp": "Write a single python function. Return ONLY python code, no prose, no tests.\n"}


def post(payload, timeout=3600, path="/v1/chat/completions"):
    rq = urllib.request.Request(
        URL + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(rq, timeout=timeout) as x:
        return json.loads(x.read())


def ask(prompt, thinking, max_tok):
    """One request -> (text, completion_tokens, elapsed_s). Raises on transport error."""
    t0 = time.time()
    if ENDPOINT == "completion":
        # No ignore_eos: let the model stop itself (filling the budget would be slow and would
        # append junk after the answer, which the "last \boxed{}" scorer can pick up).
        payload = {"prompt": prompt, "temperature": 0, "n_predict": max_tok,
                   "max_tokens": max_tok}
        if STOP:
            payload["stop"] = STOP
        r = post(payload, path="/completion")
        txt = r.get("content") or ""
        ctok = (r.get("timings") or {}).get("predicted_n") or len(r.get("tokens") or []) or 0
        return txt, ctok, time.time() - t0
    r = post({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tok,
        "chat_template_kwargs": {"enable_thinking": bool(thinking)},
    })
    msg = r["choices"][0]["message"]
    txt = msg.get("content") or msg.get("reasoning_content") or ""
    ctok = (r.get("usage") or {}).get("completion_tokens") or 0
    return txt, ctok, time.time() - t0


def load(fn):
    p = os.path.join(DATA, fn)
    if fn.endswith(".gz"):
        return [json.loads(l) for l in gzip.open(p, "rt", encoding="utf-8") if l.strip()]
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def last_int(s):
    m = NUM.findall(s or "")
    try:
        return int(m[-1].replace(",", "")) if m else None
    except Exception:
        return None


def pred_int(text):
    b = extract_boxed(text)
    if b is not None:
        v = last_int(canon_frac(b))
        if v is not None:
            return v
    return last_int(text)


def pred_letter(text):
    b = extract_boxed(text)
    if b is not None and b.strip().upper() in "ABCD":
        return b.strip().upper()
    hits = ANS.findall(text or "")
    if hits:
        return hits[-1].upper()
    hits = LET.findall((text or "").strip()[-40:])
    return hits[-1].upper() if hits else None


def strip_fence(text):
    if "```" in (text or ""):
        body = text.split("```")[1]
        return body.split("\n", 1)[1] if body.count("\n") else body
    return text or ""


def code_exec(prog, timeout):
    d = tempfile.mkdtemp(prefix="ev")
    f = os.path.join(d, "t.py")
    with open(f, "w", encoding="utf-8") as fh:
        fh.write(prog)
    try:
        r = subprocess.run([sys.executable, f], capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0 and "PASSED" in r.stdout
    except Exception:
        return False
    finally:
        shutil.rmtree(d, ignore_errors=True)


def sig_of(code):
    m = re.search(r"def\s+(\w+)\s*\(([^)]*)\)", code or "")
    return (m.group(1), m.group(2)) if m else (None, "")


def _json_ok(s):
    t = (s or "").strip()
    try:
        json.loads(t[t.find("{"):t.rfind("}") + 1] if "{" in t else t)
        return True
    except Exception:
        return False


CHECKERS = {
    "keywords:existence": lambda t, v: all(k.lower() in (t or "").lower() for k in v.get("keywords", [])),
    "punctuation:no_comma": lambda t, v: "," not in (t or ""),
    "response_format:json-format": lambda t, v: _json_ok(t),
}


def ifeval_check(text, kws):
    return all(CHECKERS[c["name"]](text, c.get("kwargs") or {}) for c in kws if c["name"] in CHECKERS)


class Ledger:
    """Per-sample JSONL records + running tally. stdout stays count-only."""

    def __init__(self, task, args):
        self.task = task
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = "%s%s_%s.jsonl" % (task, ("_" + args.tag) if args.tag else "", stamp)
        self.path = args.out or os.path.join(DATA, "results", name)
        if os.path.dirname(self.path):
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fh = open(self.path, "w", encoding="utf-8")
        self.n = self.hit = self.err = 0
        self.ctok = self.sec = 0.0

    def add(self, rec):
        self.n += 1
        if rec.get("error"):
            self.err += 1
        elif rec.get("hit"):
            self.hit += 1
        self.ctok += rec.get("ctok") or 0
        self.sec += rec.get("elapsed") or 0
        self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.fh.flush()
        if self.n % 25 == 0 or self.n <= 3:
            print("  [%s %d] hit=%s running=%.1f%%" % (self.task, self.n, bool(rec.get("hit")),
                                                       100.0 * self.hit / max(1, self.n - self.err)), flush=True)

    def close(self):
        print("SUMMARY task=%s n=%d errors=%d scored=%d hits=%d acc=%.2f%% tok_s=%.1f mean_s=%.2f out=%s" % (
            self.task, self.n, self.err, self.n - self.err, self.hit,
            100.0 * self.hit / max(1, self.n - self.err), self.ctok / max(self.sec, 1e-9),
            self.sec / max(self.n, 1), self.path), flush=True)
        self.fh.close()


def completor(args):
    """Returns f(prompt, max_tok) -> (text, ctok, elapsed). Aborts non-code tasks under selftest."""
    if args.selftest:
        if args.task not in ("humaneval", "humaneval_plus", "mbpp", "mbpp_plus", "lcb"):
            sys.exit("SELFTEST-UNSUPPORTED: only code tasks have reference completions")

        def gold(prompt, max_tok):
            return "__GOLD__", 0, 0.0
        return gold
    return lambda prompt, max_tok: ask(prompt, args.thinking, max_tok)


def task_gsm8k(L, get, a):
    rows = load("gsm8k_test.jsonl")[: (a.limit or BIG)]
    for i, ex in enumerate(rows):
        gold = last_int(ex["answer"].split("####")[-1])
        prompt = ex["question"] + "\n\nEnd with: The answer is <number>."
        rec = {"task": "gsm8k", "idx": i, "key": "gsm8k:%d" % i, "gold": gold,
               "prompt": prompt, "source": "gsm8k_test.jsonl"}
        try:
            text, ctok, el = get(prompt, a.max_tok)
            p = last_int(text)
            rec.update(pred=p, hit=(p is not None and p == gold), ctok=ctok, elapsed=el, text=text, error=None)
        except Exception as e:
            rec.update(hit=False, error=str(e)[:200])
        L.add(rec)


FRAC = re.compile(r"\\[dt]?frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}")


def canon_frac(x):
    m = FRAC.search(str(x or ""))
    if not m:
        return x
    num, den = [g for g in m.groups() if g is not None][:2]
    return "%s/%s" % (num.strip(), den.strip())


def norm(x):
    return re.sub(r"\s+", "", str(x)).replace("$", "").replace(",", "")


def _as_float(x):
    x = norm(x)
    try:
        if "/" in x:
            num, den = x.split("/", 1)
            return float(num) / float(den)
        return float(x)
    except Exception:
        return None


def _grade_math(text, gold):
    """Prefer exact normalized-string match, fall back to numeric tolerance. Handles ints,
    fractions, $-signs and thousands separators that a bare int compare would mangle."""
    p = extract_boxed(text)
    if p is None:
        nums = NUM.findall(text or "")
        p = nums[-1] if nums else None
    if p is None or gold is None:
        return p, False
    p, g = canon_frac(str(p).strip()), canon_frac(str(gold).strip())
    if norm(p) == norm(g):
        return p, True
    fp, fg = _as_float(p), _as_float(g)
    if fp is not None and fg is not None and abs(fp - fg) < 1e-6:
        return p, True
    return p, False


def _math_task(L, get, a, rows, label, tok_floor, data_file):
    for i, ex in enumerate(rows):
        gold = ex.get("answer")
        prompt = (ex.get("problem") or ex.get("question") or "") + MATH_SUFFIX
        rec = {"task": label, "idx": i, "key": "%s:%s" % (label, ex.get("unique_id", i)),
               "gold": gold, "prompt": prompt, "source": data_file}
        try:
            text, ctok, el = get(prompt, max(tok_floor, a.max_tok))
            p, hit = _grade_math(text, gold)
            rec.update(pred=p, hit=hit, ctok=ctok, elapsed=el, text=text, error=None)
        except Exception as e:
            rec.update(hit=False, error=str(e)[:200])
        L.add(rec)
    return rows


def task_math(L, get, a):
    _math_task(L, get, a, load("math500.jsonl")[: (a.limit or BIG)], "math500", 4000,
               "math500.jsonl")


def task_aime(L, get, a):
    rows = load("aime2024.jsonl") + load("aime2025.jsonl")
    _math_task(L, get, a, rows[: (a.limit or BIG)], "aime", 8000, "aime2024+2025.jsonl")


def mc_block_ceval(ex):
    return "\u95ee\u9898\uff1a%s\n%s" % (ex["question"], "\n".join("%s. %s" % (k, ex[k]) for k in "ABCD"))


def mc_block_cmmlu(ex):
    ch = ex["choices"] if isinstance(ex.get("choices"), dict) else {k: ex.get(k) for k in "ABCD"}
    return "\u95ee\u9898\uff1a%s\n%s" % (ex["question"], "\n".join("%s. %s" % (k, v) for k, v in sorted(ch.items())))


def make_mc(data_file, dev_file, label, block_fn):
    def runner(L, get, a):
        rows = load(data_file)
        if a.subject:
            rows = [r for r in rows if r.get("subject") == a.subject]
        rows = rows[: (a.limit or BIG)]
        dev = {}
        for d in load(dev_file):
            dev.setdefault(d.get("subject"), []).append(d)

        def build(ex):
            shots = "\n\n".join(block_fn(d) + "\nAnswer: " + (d.get("answer") or "").strip().upper()
                                for d in dev.get(ex.get("subject"), [])[: a.shot])
            return (shots + "\n\n" if shots else "") + block_fn(ex) + MC_SUFFIX

        for i, ex in enumerate(rows):
            subj = ex.get("subject")
            gold = (ex.get("answer") or "").strip().upper()
            prompt = build(ex)
            rec = {"task": label, "idx": i, "key": "%s:%s/%s" % (label, subj, ex.get("id", i)),
                   "subject": subj, "gold": gold, "prompt": prompt, "source": data_file}
            try:
                text, ctok, el = get(prompt, a.max_tok)
                p = pred_letter(text)
                rec.update(pred=p, hit=(p == gold), ctok=ctok, elapsed=el, text=text, error=None)
            except Exception as e:
                rec.update(hit=False, error=str(e)[:200])
            L.add(rec)
    return runner


CODE_DATA = {"humaneval": "HumanEval.jsonl.gz", "humaneval_plus": "humaneval_plus.jsonl",
             "mbpp": "mbpp.jsonl", "mbpp_plus": "mbpp_plus.jsonl"}


def code_program(ex, body, mbpp, plus):
    """Assemble the executable program for one code sample. Shared by eval_runner and ref_run so a
    quant run and the official reference run are graded by a byte-identical harness."""
    if mbpp:
        if plus:
            prog = "\n".join(ex.get("test_imports") or []) + "\n" + body + "\n" + ex["test"] + "\nprint('PASSED')\n"
        else:
            prog = (ex.get("test_setup_code") or "") + "\n" + body + "\n" + "\n".join(ex.get("test_list") or []) + "\nprint('PASSED')\n"
        return prog, (120 if plus else 30)
    call = "" if plus else ("check(%s)\n" % ex["entry_point"])
    prog = ex["prompt"] + body + "\n" + ex["test"] + "\n" + call + "print('PASSED')\n"
    return prog, (120 if plus else 30)


# SB_NOSYS=1: drop the chat-style meta instruction from code prompts. A raw /completion lane has no
# template to carry "Return ONLY python code", so the header becomes part of the continuation and the
# model answers it instead of completing the function -- the shape the monster pack kept producing
# (an indented re-`def`) came from exactly this.
CODE_NOSYS = os.environ.get("SB_NOSYS", "") == "1"


def code_prompt(ex, mbpp, name, argstr):
    head = "" if CODE_NOSYS else CODE_SYS["mbpp" if mbpp else "humaneval"]
    stem = ex.get("prompt") or ex.get("text", "")
    hint = ("\nUse exactly this signature: def %s(%s)" % (name, argstr)) if (mbpp and name) else ""
    return head + stem + hint


def task_code(L, get, a):
    mbpp = a.task.startswith("mbpp")
    plus = a.task.endswith("_plus")
    label = a.task
    rows = load(CODE_DATA[label])[: (a.limit or BIG)]
    for i, ex in enumerate(rows):
        name, argstr = sig_of(ex.get("code") or ex.get("text") or "") if mbpp else (ex.get("entry_point"), "")
        rec = {"task": label, "idx": i, "key": "%s:%s" % (label, ex.get("task_id", i)), "gold": name,
               "row": ex.get("task_id", i), "source": CODE_DATA[label], "mbpp": mbpp, "plus": plus}
        try:
            prompt = code_prompt(ex, mbpp, name, argstr)
            rec["prompt"] = prompt
            text, ctok, el = get(prompt, max(2000, a.max_tok))
            if text == "__GOLD__":
                body = ex.get("code") or ex.get("text") or ex.get("canonical_solution") or ""
            else:
                body = strip_fence(text)
            prog, tmo = code_program(ex, body, mbpp, plus)
            rec.update(hit=code_exec(prog, tmo), ctok=ctok, elapsed=el,
                       text=(None if text == "__GOLD__" else text), error=None)
        except Exception as e:
            rec.update(hit=False, error=str(e)[:200])
        L.add(rec)


def task_ifeval(L, get, a):
    rows = load("ifeval.jsonl")[: (a.limit or BIG)]
    for i, ex in enumerate(rows):
        kws = [{"name": n, "kwargs": k} for n, k in zip(ex["instruction_id_list"], ex["kwargs"])]
        rec = {"task": "ifeval_partial", "idx": i, "key": "ifeval:%s" % ex.get("key", i),
               "gold": [k["name"] for k in kws], "kw": kws, "source": "ifeval.jsonl",
               "strict": all(k["name"] in CHECKERS for k in kws)}
        rec["prompt"] = ex["prompt"]
        try:
            text, ctok, el = get(ex["prompt"], max(1500, a.max_tok))
            p = None
            rec.update(pred=p, hit=ifeval_check(text, kws), ctok=ctok, elapsed=el, text=text, error=None)
        except Exception as e:
            rec.update(hit=False, error=str(e)[:200])
        L.add(rec)


LCB_FILE = "lcb_v56.jsonl"
LCB_SYS = ("You are an expert competitive programmer. Write a complete, correct Python 3 "
           "solution. Return ONLY python code, no prose.\n")


def lcb_prompt(ex):
    if ex.get("platform") == "leetcode":
        io = "Implement class %s with the method shown in the starter code. The judge feeds " \
             "one JSON literal per line; return the value." % (ex.get("class") or "Solution")
    else:
        io = "Read input from stdin and write the answer to stdout exactly as specified."
    st = ("\nStarter code:\n```python\n%s\n```" % ex["starter_code"]) if ex.get("starter_code") else ""
    return "%s%s\n\n### Question\n%s%s\n\nEnd with a single fenced python block.\n" % (LCB_SYS, io, ex.get("content", ""), st)


def task_lcb(L, get, a):
    rows = load(LCB_FILE)
    if a.subject:
        rows = [r for r in rows if r.get("version") == a.subject]
    rows = rows[: (a.limit or BIG)]
    for i, ex in enumerate(rows):
        rec = {"task": "lcb", "idx": i, "key": ex["key"], "gold": None, "row": ex["key"],
               "source": LCB_FILE, "platform": ex["platform"], "difficulty": ex.get("difficulty"),
               "version": ex.get("version"), "tests_src": ex.get("tests_src"),
               "n_tests": len(ex.get("tests") or [])}
        try:
            prompt = lcb_prompt(ex)
            rec["prompt"] = prompt
            text, ctok, el = get(prompt, max(2500, a.max_tok))
            code = "pass" if text == "__GOLD__" else strip_fence(text)
            hit, cnt, det = lcb_judge.grade(ex, code)
            if a.selftest:
                hit = not any(k in cnt for k in ("SYS", "SKIP")) and sum(cnt.values()) > 0
            rec.update(pred=None, hit=hit, grade=cnt, grade_detail=det, ctok=ctok, elapsed=el,
                       text=(None if text == "__GOLD__" else text), error=None)
        except Exception as e:
            rec.update(hit=False, error=str(e)[:200])
        L.add(rec)


BUILDERS = {
    "gsm8k": task_gsm8k, "math": task_math, "aime": task_aime,
    "humaneval": task_code, "humaneval_plus": task_code,
    "mbpp": task_code, "mbpp_plus": task_code,
    "ifeval": task_ifeval,
    "ceval": make_mc("ceval_val.jsonl", "ceval_dev.jsonl", "ceval_val", mc_block_ceval),
    "cmmlu": make_mc("cmmlu_test.jsonl", "cmmlu_dev.jsonl", "cmmlu_test", mc_block_cmmlu),
    "lcb": task_lcb,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task", choices=list(BUILDERS))
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--thinking", type=int, default=0)
    ap.add_argument("--max-tok", type=int, default=1200)
    ap.add_argument("--shot", type=int, default=5)
    ap.add_argument("--subject", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--tag", default="")
    ap.add_argument("--selftest", action="store_true", help="canonical answers only, zero requests (code tasks)")
    a = ap.parse_args()
    L = Ledger(a.task, a)
    try:
        BUILDERS[a.task](L, completor(a), a)
    finally:
        L.close()


if __name__ == "__main__":
    main()

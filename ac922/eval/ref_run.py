#!/usr/bin/env python3
"""Reference-side runner: replay the exact prompts recorded by eval_runner.py against an
OpenAI-compatible endpoint (e.g. the hosted official Qwen), and grade with the identical scorers,
so compare_results.py can align quant-vs-official sample by sample. Zero GPU here; HTTP only.

Usage:
  export REF_API_KEY=sk-...
  python3.11 ref_run.py --from data/results/gsm8k_nvfp4.jsonl \
      --url https://dashscope.aliyuncs.com/compatible-mode/v1 --model qwen3-next \
      --thinking 0 --out data/results/gsm8k_official.jsonl
  python3.11 ref_run.py --from LEDGER --dry        # offline: regrade LEDGER's own text, no HTTP
"""
import argparse, json, os, sys, time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eval_runner as R


def call(url, model, key, prompt, thinking, max_tok, timeout=1800):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": max_tok}
    if thinking is not None:
        body["enable_thinking"] = bool(thinking)
    rq = urllib.request.Request(url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json",
                                         "Authorization": "Bearer " + (key or os.environ.get("REF_API_KEY", ""))})
    t0 = time.time()
    with urllib.request.urlopen(rq, timeout=timeout) as x:
        j = json.loads(x.read())
    txt = j["choices"][0]["message"].get("content") or ""
    ctok = (j.get("usage") or {}).get("completion_tokens") or 0
    return txt, ctok, time.time() - t0


_ROWS = {}


def row_of(source, rid):
    if source not in _ROWS:
        _ROWS[source] = {str(ex.get("task_id", i)): ex for i, ex in enumerate(R.load(source))}
    return _ROWS[source].get(str(rid))


def grade(rec, text):
    task = rec.get("task", "")
    gold = rec.get("gold")
    if task in ("humaneval", "humaneval_plus", "mbpp", "mbpp_plus"):
        ex = row_of(rec["source"], rec.get("row"))
        if ex is None:
            return None, False
        prog, tmo = R.code_program(ex, R.strip_fence(text), bool(rec.get("mbpp")), bool(rec.get("plus")))
        return None, R.code_exec(prog, tmo)
    if task == "gsm8k":
        p = R.last_int(text)
        return p, (p is not None and p == gold)
    if task in ("math500", "aime"):
        return R._grade_math(text, gold)
    if task.endswith("_val") or task.endswith("_test"):
        p = R.pred_letter(text)
        return p, (p == gold)
    if task in ("ifeval", "ifeval_partial", "ifbench"):
        kws = rec.get("kw")
        if not kws:
            raise KeyError("ledger record carries no instruction kwargs; rule tasks cannot be graded")
        return None, R.rule_grade(rec.get("prompt", ""), text, kws)[0]
    return None, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--url", default=os.environ.get("REF_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"))
    ap.add_argument("--model", default=os.environ.get("REF_MODEL", ""))
    ap.add_argument("--thinking", type=int, default=0, help="0/1, or -1 to omit the flag entirely")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.2)
    ap.add_argument("--dry", action="store_true", help="no HTTP; regrade src text to prove scorer parity")
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(a.src, encoding="utf-8") if l.strip()]
    if a.limit:
        rows = rows[: a.limit]
    todo = [r for r in rows if r.get("prompt") and "text" in r or a.dry]
    todo = [r for r in rows if r.get("prompt")] if not a.dry else rows
    if not a.dry and not a.model:
        sys.exit("REF_MODEL required for a live reference run (or use --dry)")

    fh = open(a.out, "w", encoding="utf-8")
    n = hit = err = skipped = 0
    for r in todo:
        rec = {"task": r["task"], "idx": r.get("idx"), "key": r["key"], "gold": r.get("gold"),
               "subject": r.get("subject"), "source": r.get("source"), "row": r.get("row"),
               "mbpp": r.get("mbpp"), "plus": r.get("plus"), "kw": r.get("kw"),
               "strict": r.get("strict")}
        if not a.dry:
            rec["prompt"] = r["prompt"]
        try:
            if a.dry:
                text = r.get("text") or ""
                ctok, el = 0, 0.0
            else:
                text, ctok, el = call(a.url, a.model, None, r["prompt"],
                                      None if a.thinking < 0 else a.thinking,
                                      max(1500, r.get("max_tok") or 4000))
            pred, h = grade(rec, text)
            rec.update(pred=pred, hit=h, ctok=ctok, elapsed=el, error=None,
                       text=(None if a.dry else text))
        except Exception as e:
            rec.update(hit=False, error=str(e)[:200])
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        n += 1
        hit += bool(rec.get("hit"))
        err += rec.get("error") is not None
        if n % 25 == 0 or n <= 3:
            print("  [%d] hit=%s running=%.1f%%" % (n, bool(rec.get("hit")), 100.0 * hit / max(1, n - err)), flush=True)
    fh.close()
    print("SUMMARY role=%s n=%d errors=%d hits=%d acc=%.2f%% out=%s" % (
        "dry" if a.dry else ("ref:" + a.model), n, err, hit,
        100.0 * hit / max(1, n - err), a.out), flush=True)


main()

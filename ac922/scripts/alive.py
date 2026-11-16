#!/usr/bin/env python3
"""Real-request readiness probe for an eval lane (runbook 101.6d/101.6e discipline).

/health being green only proves the process is alive. Two lanes burned time in this campaign because
nobody asked them a real question first: S1 died on every request (native verify timeout) and M1
answered but in a shape no scorer could consume. This probe asks three questions of increasing
shape-complexity and prints counts plus PASS/FAIL only -- never model text.

  tiny   arithmetic one-liner, ~20 tokens         -> proves the engine produces any completion
  math   real gsm8k sample, ~127 tokens           -> reproduces S1's crashing prompt shape
  code   real HumanEval sample, exec-graded       -> proves the humaneval path end to end

Usage: SB_URL=... SB_ENDPOINT=chat|completion SB_MODEL=... python3.11 alive.py [--only code]
Exit code 0 if every probe requested passed.
"""
import argparse, os, sys

sys.path.insert(0, os.environ.get("SB_EVALD", "/root/ac922-eval"))
import eval_runner as R


def verdict(name, ok, detail):
    print("%-6s %-5s %s" % (name, "PASS" if ok else "FAIL", detail))
    return bool(ok)


def probe_tiny():
    prompt = "What is 17 times 23? End with: The answer is <number>."
    text, ctok, el = R.ask(prompt, 0, 300)
    p = R.last_int(text)
    return verdict("tiny", p == 391, "ctok=%d pred=%s elapsed=%.1fs" % (ctok, p, el))


def probe_math():
    ex = R.load("gsm8k_test.jsonl")[0]
    gold = R.last_int(ex["answer"].split("####")[-1])
    prompt = ex["question"] + "\n\nEnd with: The answer is <number>."
    text, ctok, el = R.ask(prompt, 0, 1200)
    p = R.last_int(text)
    return verdict("math", p == gold, "ctok=%d pred=%s gold=%s elapsed=%.1fs" % (ctok, p, gold, el))


def probe_code():
    ex = R.load(R.CODE_DATA["humaneval"])[0]
    name = ex.get("entry_point")
    prompt = R.code_prompt(ex, False, name, "")
    text, ctok, el = R.ask(prompt, 0, 3000)
    body = R.strip_fence(text)
    prog, tmo = R.code_program(ex, body, False, False)
    ok = R.code_exec(prog, tmo)
    # fenced vs raw continuation is the difference between a gradeable lane and a rambling one
    return verdict("code", ok, "ctok=%d chars=%d fenced=%s elapsed=%.1fs" % (
        ctok, len(body or ""), "```" in (text or ""), el))


PROBES = {"tiny": probe_tiny, "math": probe_math, "code": probe_code}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    names = [a.only] if a.only else list(PROBES)
    print("probe url=%s endpoint=%s model=%s stops=%d" % (
        R.URL, R.ENDPOINT, R.MODEL, len(R.STOP)))
    bad = 0
    for n in names:
        try:
            if not PROBES[n]():
                bad += 1
        except Exception as e:
            bad += 1
            print("%-6s %-5s %s" % (n, "ERROR", str(e)[:120]))
    print("ALIVE %s failed=%d" % ("OK" if bad == 0 else "NO", bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

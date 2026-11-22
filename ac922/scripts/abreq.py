#!/usr/bin/env python3.11
# abreq.py - same-boot A/B harness for strata_tune request-level switches.
#
# The engine reads sampling.strata_tune={pcie_frac|spec_min_p|prefill_pipe|aux_cpus},
# sampling.strata_prefix={"tokens":N} and sampling.strata_checkpoint:false per request
# (serve/server.py:1674), so both arms run in ONE boot: no cross-boot codegen/topology
# noise, which is what killed the earlier env-level verdicts (discipline: an effect
# below 4 ms/window must never be judged on a cross-boot comparison).
#
# Protocol (paired, alternating): rounds x (A then B, order flipped per round); per arm
# >= windows requests each.  Statistic = per-round arm medians, paired across rounds;
# verdict = paired medians + two-sided exact sign test (p<0.05).  Never prints prompt,
# completion or template text - only numbers.
import argparse, json, statistics, sys, time, urllib.request

def post(url, payload, key):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.loads(r.read().decode())

def sign_test_p(diffs):
    # exact two-sided sign test on nonzero paired diffs
    n = len(diffs)
    if n == 0: return 1.0
    k = sum(1 for d in diffs if d > 0)
    from math import comb
    p_one = sum(comb(n, i) for i in range(k, n + 1)) / (2 ** n)
    p = min(1.0, 2 * min(p_one, sum(comb(n, i) for i in range(0, k + 1)) / (2 ** n)))
    return p

def run_arm(base, model, key, prompt_file, max_tokens, tune, windows, extra_sampling):
    prompt = open(prompt_file).read()
    times, tps, acc = [], [], None
    for _ in range(windows):
        sampling = {"temperature": 0.0, "max_tokens": max_tokens}
        if tune: sampling["strata_tune"] = tune
        sampling.update(extra_sampling or {})
        body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                "sampling": sampling, "stream": False}
        t0 = time.perf_counter()
        j = post(base + "/v1/chat/completions", body, key)
        dt = time.perf_counter() - t0
        n = (j.get("usage") or {}).get("completion_tokens") or 0
        if dt > 0 and n: times.append(dt); tps.append(n / dt)
        u = j.get("usage") or {}
        if "drafts_accepted" in u: acc = (u.get("drafts_accepted"), u.get("drafts_total"))
    return {"ms_per_window": statistics.median(times) * 1000 if times else None,
            "tg": statistics.median(tps) if tps else None, "n": len(times), "acc": acc}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8091")
    ap.add_argument("--model", default="strata-nvfp4")
    ap.add_argument("--key", default="")
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--windows", type=int, default=30)
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--arm-a", required=True, help="label=json|empty, json = strata_tune dict")
    ap.add_argument("--arm-b", required=True)
    ap.add_argument("--extra-sampling-a", default="")
    ap.add_argument("--extra-sampling-b", default="")
    a = ap.parse_args()
    def parse_arm(spec, extra):
        label, _, js = spec.partition("=")
        tune = json.loads(js) if js else {}
        xs = json.loads(extra) if extra else {}
        return label, tune, xs
    la, ta, xa = parse_arm(a.arm_a, a.extra_sampling_a)
    lb, tb, xb = parse_arm(a.arm_b, a.extra_sampling_b)
    import os
    key = a.key or os.environ.get("STRATA_API_KEY") or open("/data/work/lane.key").read().strip()
    rows = []
    for r in range(a.rounds):
        order = [(ta, xa), (tb, xb)] if r % 2 == 0 else [(tb, xb), (ta, xa)]
        ra = run_arm(a.base, a.model, key, a.prompt_file, a.max_tokens, order[0][0], a.windows, order[0][1])
        rb = run_arm(a.base, a.model, key, a.prompt_file, a.max_tokens, order[1][0], a.windows, order[1][1])
        if order[0] == (ta, xa): ma, mb = ra, rb
        else: mb, ma = ra, rb
        rows.append({"round": r, la: ma, lb: mb})
        print(json.dumps(rows[-1]))
        sys.stdout.flush()
    med = lambda arm: statistics.median([x[arm]["ms_per_window"] for x in rows if x[arm]["ms_per_window"]])
    diffs = [x[la]["ms_per_window"] - x[lb]["ms_per_window"] for x in rows
             if x[la]["ms_per_window"] and x[lb]["ms_per_window"]]
    p = sign_test_p(diffs)
    dm = (med(la) - med(lb)) if diffs else None
    summary = {"arm_a": la, "arm_b": lb, "median_ms_a": med(la), "median_ms_b": med(lb),
               "delta_ms": dm, "pct": (dm / med(lb) * 100) if dm is not None else None,
               "p_sign": p, "verdict": None,
               "note": "sub-4ms/window: same-boot only, never judge across boots"}
    if dm is not None and abs(dm) < 4: summary["verdict"] = "below-ruler (>=4ms/window rule)"
    elif p < 0.05: summary["verdict"] = ("A slower" if dm > 0 else "A faster")
    else: summary["verdict"] = "not significant"
    print(json.dumps({"summary": summary}))

if __name__ == "__main__":
    main()

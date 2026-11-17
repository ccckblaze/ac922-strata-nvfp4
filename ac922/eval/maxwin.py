"""The exact window limit, measured rather than assumed.

Why this exists: `aggbench.calibrate()` and the first `fullwindow.py` converge on a token target by
rebuilding whole ~24-token units, so a target just above the engine's ceiling looks like a failure
and gets reported as a refusal when the request actually succeeded. Measured 2026-11-17: targets
261,848-261,866 came back `finish_reason=length` (accepted, generated) while the calibrator gave up.
That cannot answer "does it hold the window open to the last token", because it cannot tell a
refusal from its own non-convergence.

This tool makes the knob strictly monotone instead: build any base payload, then grow it one token
at a time with a one-token filler, and first PROVE that k fillers move prompt_tokens by exactly k.
Only then does it search for the ceiling.

modes
  linearity          prove prompt_tokens(base + k fillers) == base + k for k = 0, 5, 20
  bound              find the largest accepted prompt exactly, by fill steps around the cap
  run --n 6          n concurrent streams at prompt = --total - --gen, each asked to generate all
                     of --gen; a stream that returns finish=length with ctok == gen consumed the
                     last slot of the window, which is the claim being tested

usage: maxwin.py linearity [--base 260000]
       maxwin.py bound [--target 262143]
       maxwin.py run --n 6 [--total 262144] [--gen 299] [--warm 1]
env: AB_URL  STRATA_API_KEY  AB_TIMEOUT
Integers, labels and hashes only - never model text.
"""
import json, os, re, sys, threading, time, urllib.error, urllib.request

URL = os.environ.get("AB_URL", "http://127.0.0.1:8091")
KEY = os.environ.get("STRATA_API_KEY", "")
TMO = float(os.environ.get("AB_TIMEOUT", "3600"))
MODEL = "qwen3.8-flash-next"
FILL = ("Reimagine a lighthouse keeper logging nightly waves and distant ships; "
        "the ledger grows one careful paragraph at a time. ")
ONE = " 1"


def flag(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def post(text, gen):
    body = {"model": MODEL, "messages": [{"role": "user", "content": text}],
            "temperature": 0, "max_tokens": gen, "stream": True,
            "stream_options": {"include_usage": True}}
    hd = {"Content-Type": "application/json"}
    if KEY:
        hd["Authorization"] = "Bearer " + KEY
    req = urllib.request.Request(URL + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers=hd)
    t0 = time.time()
    first = last = None
    chunks = 0
    fin = ptok = ctok = None
    try:
        with urllib.request.urlopen(req, timeout=TMO) as x:
            for raw in x:
                ln = raw.decode().strip()
                if not ln.startswith("data:"):
                    continue
                p = ln[5:].strip()
                if p == "[DONE]":
                    break
                try:
                    o = json.loads(p)
                except ValueError:
                    continue
                if o.get("usage"):
                    u = o["usage"]
                    ptok, ctok = u.get("prompt_tokens"), u.get("completion_tokens")
                for ch in o.get("choices", []):
                    d = ch.get("delta") or {}
                    if sum(len(str(d.get(k) or "")) for k in ("content", "reasoning_content")):
                        chunks += 1
                        first = time.time() - t0 if first is None else first
                        last = time.time() - t0
                    if ch.get("finish_reason"):
                        fin = ch["finish_reason"]
    except urllib.error.HTTPError as e:
        # a refusal body can quote request text; keep its integers only
        try:
            nums = [int(m) for m in re.findall(rb"\d{2,}", e.read()[:4000])][:8]
        except Exception:
            nums = []
        return {"ok": False, "http": e.code, "nums": nums, "ptok": None, "ctok": None,
                "finish": "http-%d" % e.code, "ttft": None, "tg": None, "span": round(time.time()-t0, 2)}
    except Exception as e:
        return {"ok": False, "http": None, "ptok": ptok, "ctok": ctok, "finish": type(e).__name__,
                "ttft": first, "tg": None, "span": round(time.time()-t0, 2)}
    c = ctok or chunks
    tg = round(c / (last - first), 2) if c and last and first is not None and last > first else None
    return {"ok": True, "http": None, "ptok": ptok, "ctok": c, "chunks": chunks, "finish": fin,
            "ttft": round(first, 2) if first is not None else None, "tg": tg,
            "span": round(time.time() - t0, 2)}


def base_payload(approx_tokens, marker):
    """Coarse payload near a token count; exactness comes from fillers, never from this."""
    return marker + FILL * max(1, int(approx_tokens / 24))


def with_fill(text, k):
    return text + ONE * k if k > 0 else text


def linearity():
    approx = int(flag("--base", "260000"))
    marker = "Linearity marker. "
    text = base_payload(approx, marker)
    r = post(text, 1)
    if not r["ok"]:
        raise SystemExit("BASE-FAILED http=%s finish=%s" % (r["http"], r["finish"]))
    b = r["ptok"]
    print("base ptok=%d" % b, flush=True)
    good = []
    for k in (0, 5, 20):
        rr = post(with_fill(text, k), 1)
        okk = rr["ok"] and rr["ptok"] == b + k
        good.append(okk)
        print("k=%2d ptok=%s delta=%s expect=%s %s" %
              (k, rr["ptok"], (rr["ptok"] - b) if rr["ptok"] else None, k,
               "LINEAR" if okk else "NON-LINEAR"), flush=True)
    print("LINEARITY-" + ("OK" if all(good) else "FAIL"))
    return b, all(good)


def bound():
    target = int(flag("--target", "262143"))
    text = base_payload(target - 200, "Bound marker. ")
    r = post(text, 1)
    if not r["ok"]:
        raise SystemExit("BASE-FAILED http=%s" % r["http"])
    b = r["ptok"]
    print("bound base ptok=%d target=%d" % (b, target), flush=True)
    # Acceptance is "the engine ran it", read from the usage chunk - NOT "prompt_tokens moved by k".
    # A filler is not guaranteed to be one token (`" 1"` measured +2.05 tokens per copy: +11 for 5,
    # +41 for 20), so the returned ptok is the only number worth trusting, and the bisect runs on
    # the monotone knob (filler count) while reporting the engine's own prompt length.
    lo_k, hi_k, last_ptok = 0, None, b

    def trial(k):
        rr = post(with_fill(text, k), 1)
        acc = bool(rr["ok"])
        print("  fill=%6d ptok=%s %s%s" % (k, rr["ptok"], "accept" if acc else "REJECT",
              "" if acc else " http=%s nums=%s" % (rr["http"], rr.get("nums"))), flush=True)
        return acc, (rr["ptok"] or last_ptok)

    k = 8
    while True:                                  # exponential search for the first refusal
        acc, last_ptok = trial(k)
        if acc:
            lo_k, k = k, k * 2
            if k > 4096:
                break
        else:
            hi_k = k
            break
    if hi_k is None:
        print("BOUNDS-NO-REFUSAL fill=%d ptok=%d" % (lo_k, last_ptok))
        return last_ptok
    while lo_k + 1 < hi_k:                       # bisect between the last accept and first reject
        mid = (lo_k + hi_k) // 2
        acc, p = trial(mid)
        if acc:
            lo_k, last_ptok = mid, p
        else:
            hi_k = mid
    acc, above = (None, None)
    print("MAX-ACCEPTED-PROMPT %d (fill=%d); the next accepted step is refused -> gen=%d fills the "
          "window exactly (%d + %d = %d)" % (last_ptok, lo_k, 262144 - last_ptok, last_ptok,
                                             262144 - last_ptok, 262144), flush=True)
    with open("/data/work/note312/maxwin.ptok", "w") as f:
        f.write(str(lo_k))
    return last_ptok


def run():
    """Fill the window to the last token per stream: gen_i = total - ptok_i.

    Two measured facts drive this (2026-11-17, `bound` plus ARM-A of `run`):

    - The engine refuses a request whose prompt + max_tokens exceeds `--total` (refusal body
      quotes both numbers), so exactness is a property of the SUM, not of hitting a round prompt
      count - which matters because a filler is not one token (`" 1"` measures +2.05).
    - The engine never writes past position 262,136 = 262,144 - 8. Two independent proofs bite
      together: prompt 262,136 with gen 1 is refused, and five concurrent streams asked for
      gen 437 were cut at ctok 429, i.e. exactly at position 262,136. The reserve is the MTP
      draft window (`--spec 6` plus slack). So `--total` is the *configured* window and
      `--seq-ceiling` is the *addressable* one; the byte-exact full-window test targets the latter,
      and asking for the former overshoots by exactly the reserve.

    A stream consumed the whole window when finish_reason=length and ptok + ctok == seq_ceiling.
    """
    n = int(flag("--n", "6"))
    total = int(flag("--total", "262144"))
    ceiling = int(flag("--seq-ceiling", "262136"))
    cap = flag("--cap-ptok")                      # only test streams that fit under this prompt
    warm = flag("--warm", "1") == "1"
    target = int(flag("--prompt", str(total - 200)))
    text = base_payload(target - 100, "Stream 0 of %d marker. " % n)
    r = post(text, 1)
    if not r["ok"]:
        raise SystemExit("BASE-FAILED http=%s" % r["http"])
    b, k = r["ptok"], target - r["ptok"]
    if k < 0:
        raise SystemExit("BASE-TOO-LONG ptok=%d target=%d" % (b, target))
    # one calibration serves every stream because all markers carry the same token count; the warm
    # step below asserts that per stream instead of trusting it
    fill_file = "/data/work/note312/maxwin.ptok"
    k = int(flag("--fill", open(fill_file).read().strip() if os.path.exists(fill_file) else "8"))
    text = with_fill(base_payload(target - 400, "Stream 0 of %d marker. " % n), k)
    rr = post(text, 1)
    if not rr["ok"]:
        raise SystemExit("CALIB-FAILED http=%s nums=%s" % (rr["http"], rr.get("nums")))
    payloads = [with_fill(base_payload(rr["ptok"] - 400, "Stream %d of %d marker. " % (i, n)), k)
                for i in range(n)]
    print("calib fill=%d ptok=%d (ceiling=%d -> reserve=%d)" %
          (k, rr["ptok"], ceiling, ceiling - rr["ptok"]), flush=True)
    gens, ptoks = [], []
    for i, p in enumerate(payloads):
        w = post(p, 1)
        if not w["ok"]:
            raise SystemExit("STREAM-%d-BASELINE-REJECTED http=%s nums=%s"
                             % (i, w["http"], w.get("nums")))
        if ptoks and w["ptok"] != ptoks[0]:
            raise SystemExit("STREAM-%d-PROMPT-DIFFERS %s vs %s (markers are not token-parallel)"
                             % (i, w["ptok"], ptoks[0]))
        ptoks.append(w["ptok"])
        gens.append(ceiling - w["ptok"])
        if cap and w["ptok"] > int(cap):
            raise SystemExit("STREAM-%d-OVER-CAP ptok=%d cap=%s" % (i, w["ptok"], cap))
        print("warm %d ptok=%d gen_to_fill_window=%d (position %d) ttft=%s" %
              (i, w["ptok"], gens[-1], ceiling, w["ttft"]), flush=True)
    target, gen = ptoks[0], gens[0]
    out = [None] * n

    def work(i):
        out[i] = post(payloads[i], gens[i])
    th = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    t0 = time.time()
    for t in th:
        t.start()
    for t in th:
        t.join()
    span = round(time.time() - t0, 2)
    tgs = [x["tg"] for x in out if x.get("tg")]
    pos = [(x.get("ptok") or 0) + (x.get("ctok") or 0) for x in out]
    consumed = [bool(x["ok"] and x["finish"] == "length" and p == ceiling)
                for x, p in zip(out, pos)]
    print(json.dumps({
        "n": n, "prompt": target, "gen": gen, "total": total,
        "seq_ceiling": ceiling,
        "invariant_prompt_plus_gen_equals_ceiling": all(t + g == ceiling for t, g in zip(ptoks, gens)),
        "max_position_reached": max([p for p in pos if p]) if pos else None,
        "span": span, "ptok": [x.get("ptok") for x in out],
        "all_prompt_exact": all(x.get("ptok") == target for x in out),
        "ctok": [x.get("ctok") for x in out],
        "finish": [x.get("finish") for x in out],
        "streams_that_consumed_the_whole_window": sum(consumed),
        "gen_per_stream": gens,
        "prompt_plus_ctok": [(x.get("ptok") or 0) + (x.get("ctok") or 0) for x in out],
        "ttft": [x.get("ttft") for x in out], "tg": tgs,
        "sum_tg": round(sum(tgs), 1) if tgs else None,
        "tg_min": min(tgs) if tgs else None, "tg_med": (sorted(tgs)[len(tgs)//2] if tgs else None),
        "tg_max": max(tgs) if tgs else None,
        "errors": sorted(set(str(x.get("finish")) for x in out if not x["ok"])) or None,
    }, sort_keys=True))


mode = sys.argv[1] if len(sys.argv) > 1 else "linearity"
{"linearity": linearity, "bound": bound, "run": run}[mode]()

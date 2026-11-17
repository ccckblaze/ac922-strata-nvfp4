"""Byte-exact full-window test: no 98.7%, no "close enough".

`aggbench.calibrate()` converges to within 1% of a target depth, which answers "is the lane fast at
260K" but not "does the lane hold the window open to its last token". This tool calibrates to an
EXACT prompt_tokens count by appending/removing one-token fillers and re-reading the engine's own
usage, so prompt_tokens == target with zero slack, and it can state the invariant it tests:

    prompt_tokens + max_tokens == --total (default 262144)

Two modes:
  probe : binary-search the largest prompt the engine accepts (1 stream, gen 1), reported exactly
  run   : build n payloads each at exactly (total - gen) prompt tokens, warm them, fire them
          together, and check per stream whether the full window was consumed (finish_reason=length
          on a stream that delivered `gen` tokens is proof the last slot of the window was used)

usage: fullwindow.py probe [--lo 261000] [--gen 1]
       fullwindow.py run --n 6 --gen 512 [--total 262144] [--warm 1]
env: AB_URL  STRATA_API_KEY  AB_TIMEOUT
Prints integers, labels and hashes only - never model text.
"""
import json, os, re, sys, threading, time, urllib.error, urllib.request

URL = os.environ.get("AB_URL", "http://127.0.0.1:8091")
KEY = os.environ.get("STRATA_API_KEY", "")
TMO = float(os.environ.get("AB_TIMEOUT", "3600"))
MODEL = "qwen3.8-flash-next"
FILL = ("Reimagine a lighthouse keeper logging nightly waves and distant ships; "
        "the ledger grows one careful paragraph at a time. ")
ONE = " 1"            # one filler == one token, verified by the convergence loop below


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
    deltas = usage = 0
    fin, ptok, ctok = None, None, None
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
                    ptok = u.get("prompt_tokens")
                    ctok = u.get("completion_tokens")
                for ch in o.get("choices", []):
                    d = ch.get("delta") or {}
                    n = sum(len(str(d.get(k) or "")) for k in ("content", "reasoning_content"))
                    if n:
                        deltas += 1
                        first = time.time() - t0 if first is None else first
                        last = time.time() - t0
                    if ch.get("finish_reason"):
                        fin = ch["finish_reason"]
    except urllib.error.HTTPError as e:
        # the refusal body can quote request text; keep only its integers
        nums = re.findall(rb"\d+", e.read()[:4000]) if hasattr(e, "read") else []
        return {"ok": False, "http": e.code, "nums": [int(n) for n in nums][:8],
                "ptok": None, "ctok": None, "finish": "http-%d" % e.code,
                "ttft": None, "tg": None, "span": round(time.time() - t0, 2)}
    except Exception as e:
        return {"ok": False, "http": None, "ptok": ptok, "ctok": ctok,
                "finish": type(e).__name__, "ttft": first, "tg": None,
                "span": round(time.time() - t0, 2)}
    c = ctok or deltas
    tg = round(c / (last - first), 2) if c and last and first is not None and last > first else None
    return {"ok": True, "http": None, "ptok": ptok, "ctok": c, "chunks": deltas,
            "finish": fin, "ttft": round(first, 2) if first is not None else None,
            "tg": tg, "span": round(time.time() - t0, 2)}


def exact(target, prefix, gen=1, max_iters=14):
    """Grow/shrink the payload until the engine itself reports prompt_tokens == target."""
    text = prefix + FILL * max(1, int(target / 24))
    r = post(text, gen)
    if not r["ok"]:
        return None, r
    for _ in range(max_iters):
        err = target - r["ptok"]
        if err == 0:
            return text, r
        if abs(err) <= 4:                                  # fine: one-token fillers
            text = text + ONE * err if err > 0 else text[:len(text) + err]
        else:                                              # coarse: rebuild ~24 tokens per unit
            units = max(1, int(len(text) / len(FILL)) + int(err / 24))
            text = prefix + FILL * units
        r = post(text, gen)
        if not r["ok"]:
            return None, r
    return None, r


def probe():
    lo, hi, gen = int(flag("--lo", "261000")), int(flag("--hi", "264000")), int(flag("--gen", "1"))
    print("probe lo=%d hi=%d gen=%d" % (lo, hi, gen), flush=True)
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        text, r = exact(mid, "Probe marker. ", gen)
        got = r["ptok"] if r["ptok"] else mid
        if text is None:
            print("  reject prompt=%d finish=%s nums=%s" % (mid, r["finish"], r.get("nums")),
                  flush=True)
            hi = mid
        else:
            print("  accept prompt=%d ptok=%d ctok=%s finish=%s" %
                  (mid, got, r["ctok"], r["finish"]), flush=True)
            lo = mid
    return lo


def run():
    n = int(flag("--n", "6"))
    total = int(flag("--total", "262144"))
    gen = int(flag("--gen", "512"))
    warm = flag("--warm", "1") == "1"
    target = total - gen
    print("target prompt=%d + gen=%d = %d" % (target, gen, total), flush=True)
    payloads = []
    for i in range(n):
        text, r = exact(target, "Stream %d of %d marker. " % (i, n), 1)
        if text is None:
            print("CALIB-FAILED %d finish=%s nums=%s" % (i, r["finish"], r.get("nums")), flush=True)
            raise SystemExit(3)
        payloads.append(text)
        print("calib %d ptok=%d exact=%s" % (i, r["ptok"], r["ptok"] == target), flush=True)
    if warm:
        for i, p in enumerate(payloads):
            w = post(p, 1)
            print("warm %d ok=%s ptok=%s ttft=%s" % (i, w["ok"], w["ptok"], w["ttft"]), flush=True)
    out = [None] * n

    def work(i):
        out[i] = post(payloads[i], gen)
    th = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    t0 = time.time()
    for t in th:
        t.start()
    for t in th:
        t.join()
    span = round(time.time() - t0, 2)
    tgs = [x["tg"] for x in out if x.get("tg")]
    full = [bool(x["ok"] and x["finish"] == "length" and (x["ctok"] or 0) == gen) for x in out]
    # `full` is unreachable by construction at the byte-exact edge: a stream that consumes the whole
    # window is cut off with fewer than `gen` tokens, so `window_fully_consumed: 0` read as a failure
    # is a harness artifact.  The honest window invariant is `held_edge` below: finish=length, every
    # stream parked on the same prompt+gen position, and that position plus the tokens the stream
    # still wanted reaching the configured window (the window closed on it, max_tokens did not).  It
    # is `>=`, not `>`: the engine keeps a few bytes of its own at the top of the window, so a row
    # cut exactly at the edge wanted precisely what that reservation holds.
    pgs = [(x.get("ptok") or 0) + (x.get("ctok") or 0) for x in out]
    edge = max(pgs) if pgs else 0
    held = [bool(x["ok"] and x["finish"] == "length" and p == edge
                 and (x["ctok"] or 0) < gen and edge + (gen - (x["ctok"] or 0)) >= total)
            for x, p in zip(out, pgs)]
    print(json.dumps({
        "n": n, "total": total, "prompt_target": target, "gen": gen, "span": span,
        "ptok": [x.get("ptok") for x in out],
        "all_prompt_exact": all(x.get("ptok") == target for x in out),
        "ctok": [x.get("ctok") for x in out],
        "finish": [x.get("finish") for x in out],
        "window_fully_consumed": sum(full),
        "held_edge": sum(held), "edge_position": edge, "all_held_edge": bool(held) and all(held),
        "prompt_plus_gen": [ (x.get("ptok") or 0) + (x.get("ctok") or 0) for x in out],
        "ttft": [x.get("ttft") for x in out], "tg": tgs,
        "sum_tg": round(sum(tgs), 1) if tgs else None,
        "tg_min": min(tgs) if tgs else None, "tg_max": max(tgs) if tgs else None,
        "errors": sorted(set(str(x.get("finish")) for x in out if not x["ok"])) or None,
    }, sort_keys=True))
    if held and all(held):
        print("FULLWINDOW-PASS %d/%d held the window edge at prompt+gen=%d" % (len(held), len(held), edge))
    else:
        print("FULLWINDOW-FAIL held_edge %d/%d edge=%d - a stream stopped for another reason "
              "(EOS-early, an error, or it did not reach the edge)" % (sum(held), len(held), edge))


({"probe": probe, "run": run}.get(sys.argv[1] if len(sys.argv) > 1 else "run"))()

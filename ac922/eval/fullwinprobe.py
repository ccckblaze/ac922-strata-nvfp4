"""Full-window concurrency probe: does the lane really truncate deep streams?

aggbench counts SSE deltas that carry content as if one delta == one token. Under MTP drafting one
delta can carry more than one verified token, so its `ctok` under-reads the generation, and the
"refuse to score a short stream" guard then drops every deep stream and makes the lane look broken.
This probe separates the two hypotheses with the engine's own numbers:

  chunks    = SSE deltas that carried content (what aggbench counts)
  ctok_use  = usage.completion_tokens (the engine's own count, from the usage chunk)
  finish    = finish_reason (stop | length | tool_calls | ...)
  tok_per_chunk = ctok_use / chunks: ~1.0 when a stream genuinely stopped at the asked length,
                  >1 when the shortfall is a multi-token-delta artifact, not a truncation.

usage: fullwinprobe.py --n 6 --depth 260000 --gen 1024 [--warm 1]
env:   AB_URL  STRATA_API_KEY  AB_TIMEOUT
Prints numbers and labels only - never model text.
"""
import json, os, sys, threading, time, urllib.error, urllib.request

URL = os.environ.get("AB_URL", "http://127.0.0.1:8091")
KEY = os.environ.get("STRATA_API_KEY", "")
TMO = float(os.environ.get("AB_TIMEOUT", "1800"))
MODEL = "qwen3.8-flash-next"
FILL = ("Reimagine a lighthouse keeper logging nightly waves and distant ships; "
        "the ledger grows one careful paragraph at a time. ")


def flag(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def run(text, gen):
    body = {"model": MODEL, "messages": [{"role": "user", "content": text}],
            "temperature": 0, "max_tokens": gen, "stream": True,
            "stream_options": {"include_usage": True}}
    hd = {"Content-Type": "application/json"}
    if KEY:
        hd["Authorization"] = "Bearer " + KEY
    req = urllib.request.Request(URL + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers=hd)
    t0 = time.time()
    chunks = first = last = None
    usage, finish = {}, None
    try:
        with urllib.request.urlopen(req, timeout=TMO) as x:
            for raw in x:
                ln = raw.decode().strip()
                if not ln.startswith("data:"):
                    continue
                payload = ln[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except ValueError:
                    continue
                if obj.get("usage"):
                    usage = obj["usage"]
                for ch in obj.get("choices", []):
                    d = ch.get("delta") or {}
                    n = sum(len(str(d.get(k) or "")) for k in ("content", "reasoning_content"))
                    if n:
                        chunks = (chunks or 0) + 1
                        first = time.time() - t0 if first is None else first
                        last = time.time() - t0
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SystemExit("PROBE-ERROR auth-%d: export STRATA_API_KEY" % e.code)
        return {"chunks": 0, "ctok_use": None, "ptok": None, "finish": "http-%d" % e.code,
                "ttft": None, "span": round(time.time() - t0, 2)}
    except Exception as e:
        return {"chunks": chunks or 0, "ctok_use": usage.get("completion_tokens"),
                "ptok": usage.get("prompt_tokens"), "finish": type(e).__name__,
                "ttft": first, "span": round(time.time() - t0, 2)}
    ctok = usage.get("completion_tokens")
    tg = (round(ctok / (last - first), 2)
          if ctok and last and first is not None and last > first else None)
    return {"chunks": chunks or 0, "ctok_use": ctok, "ptok": usage.get("prompt_tokens"),
            "cached": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
            "finish": finish, "ttft": round(first, 2) if first is not None else None,
            "tg": tg, "span": round(time.time() - t0, 2)}


def burst(payloads, gen):
    out = [None] * len(payloads)

    def work(i):
        out[i] = run(payloads[i], gen)
    th = [threading.Thread(target=work, args=(i,)) for i in range(len(payloads))]
    t0 = time.time()
    for t in th:
        t.start()
    for t in th:
        t.join()
    return out, round(time.time() - t0, 2)


def main():
    n = int(flag("--n", "6"))
    depth = int(flag("--depth", "260000"))
    gen = int(flag("--gen", "1024"))
    warm = flag("--warm", "1") == "1"
    units = 20
    r = run(FILL * units, 1)
    per = r["ptok"] / units
    for _ in range(3):
        units = max(units, int((depth - 200) / per / units) * units)
        r = run(("Stream 0 of %d marker. " % n) + FILL * units, 1)
        per = r["ptok"] / units
        if abs(r["ptok"] - depth) <= 0.05 * depth:
            break
    payloads = [("Stream %d of %d marker. " % (i, n)) + FILL * units for i in range(n)]
    print("calib units=%d ptok=%s" % (units, r["ptok"]), flush=True)
    if warm:
        for i, p in enumerate(payloads):
            w = run(p, 1)
            print("warm %d ptok=%s cached=%s ttft=%s" % (i, w["ptok"], w.get("cached"),
                                                         w["ttft"]), flush=True)
    res, span = burst(payloads, gen)
    tgs = [x["tg"] for x in res if x.get("tg")]
    print(json.dumps({
        "n": n, "depth_target": depth, "gen_asked": gen, "span": span,
        "ctok_use": [x.get("ctok_use") for x in res],
        "chunks": [x.get("chunks") for x in res],
        "finish": [x.get("finish") for x in res],
        "ptok": [x.get("ptok") for x in res],
        "cached": [x.get("cached") for x in res],
        "ttft": [x.get("ttft") for x in res],
        "tg": tgs, "sum_tg": round(sum(tgs), 1) if tgs else None,
        "tok_per_chunk": [round(x["ctok_use"] / x["chunks"], 2)
                          if x.get("ctok_use") and x.get("chunks") else None for x in res],
    }, sort_keys=True))


main()

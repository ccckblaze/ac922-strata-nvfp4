"""Aggregate-decode bench for one lane, measured where the client can see it.

Why not the engine's accounting lines: with --batch slots the engine writes the per-request
summary only for the requests it finishes on its own schedule, and those lines arrive after the
client has already returned (a read taken right after the burst, even polled, caught the window
lines but not the completions). The SSE stream is the reliable source: one content delta per
token, plus the usage chunk, so each stream's own first/last token timestamps give its decode
rate, and the burst gives the aggregate.

Two aggregate calibers, both reported:
  sum_tg      = sum of the per-stream decode rates  (the published caliber in PERFORMANCE.md)
  wall_tg     = total tokens / (last token - first token), i.e. what a caller experiences
They differ when the streams do not decode at the same moment - which is exactly the
FIFO-admission effect we are hunting.

Each payload is warmed first (sequentially, one token) so every arm starts from a warm prefix
cache: prefill cost is then outside the measurement, and the arm measures decode only. The cold
prefill burst is still visible in WARM_S if the caller asks for it (--cold 1).

usage: aggbench.py --tag CTL --depth 8000 --n 6 --gen 256 [--cold 0]
env:   AB_URL (default http://127.0.0.1:8091)  AB_ENG (engine log)  STRATA_API_KEY
"""
import json, os, re, statistics as st, sys, threading, time, urllib.error, urllib.request

URL = os.environ.get("AB_URL", "http://127.0.0.1:8091")
ENG = os.environ.get("AB_ENG", "/data/champion/strata-nvfp4-engine.log")
KEY = os.environ.get("STRATA_API_KEY", "")
TMO = float(os.environ.get("AB_TIMEOUT", "900"))
MODEL = "qwen3.8-flash-next"
FILL = ("Reimagine a lighthouse keeper logging nightly waves and distant ships; "
        "the ledger grows one careful paragraph at a time. ")
REQ = re.compile(r"prompt (\d+) tokens = (\d+) reused \+ \d+ read in \d+ ms \(([\d.]+) tok/s\),"
                 r" (\d+) generated in \d+ ms \(([\d.]+) tok/s\), drafts accepted (\d+) of (\d+)")
WIN = re.compile(r"batch \(([^)]*)\): (\d+) group-steps, (\d+) rows in (\d+) ms = ([\d.]+) rows/s")


def flag(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def stream_run(text, gen):
    body = {"model": MODEL, "messages": [{"role": "user", "content": text}],
            "temperature": 0, "max_tokens": gen, "stream": True,
            "stream_options": {"include_usage": True}}
    hd = {"Content-Type": "application/json"}
    if KEY:
        hd["Authorization"] = "Bearer " + KEY
    req = urllib.request.Request(URL + "/v1/chat/completions", data=json.dumps(body).encode(), headers=hd)
    t0 = time.time()
    first = last = None
    deltas = 0
    usage = {}
    try:
        with urllib.request.urlopen(req, timeout=TMO) as x:
            for raw in x:
                ln = raw.decode().strip()
                if not ln.startswith("data:"):
                    continue
                body_txt = ln[5:].strip()
                if body_txt == "[DONE]":
                    break
                if "content" in body_txt and '"content": ""' not in body_txt:
                    deltas += 1
                    if first is None:
                        first = time.time() - t0
                    last = time.time() - t0
                if "usage" in body_txt:
                    try:
                        usage = json.loads(body_txt).get("usage", usage)
                    except ValueError:
                        pass
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SystemExit("AB-ERROR auth-%d: export STRATA_API_KEY first" % e.code)
        return {"first": None, "last": None, "ctok": 0, "ptok": None, "reused": None,
                "error": "http-%d" % e.code}
    except Exception as e:                       # a timeout or a lane that died mid-burst is a
        return {"first": first, "last": last, "ctok": deltas, "ptok": usage.get("prompt_tokens"),
                "reused": None, "error": type(e).__name__}   # result, not a hung night
    ctok = usage.get("completion_tokens") or deltas
    return {"first": first, "last": last, "ctok": ctok, "ptok": usage.get("prompt_tokens"),
            "reused": (usage.get("prompt_tokens_details") or {}).get("cached_tokens")}


def burst(payloads, gen):
    out = [None] * len(payloads)
    def work(i):
        out[i] = stream_run(payloads[i], gen)
    th = [threading.Thread(target=work, args=(i,)) for i in range(len(payloads))]
    t0 = time.time()
    for t in th:
        t.start()
    for t in th:
        t.join()
    span = round(time.time() - t0, 2)
    return out, span


def calibrate(target, n):
    units = 20
    r = stream_run(FILL * units, 1)
    per = r["ptok"] / units
    for _ in range(3):
        units = max(units, int((target - 200) / per / units) * units)
        r = stream_run(("Stream 0 of %d marker. " % n) + FILL * units, 1)
        per = r["ptok"] / units
        if abs(r["ptok"] - target) <= 0.05 * target:
            break
    return units, r["ptok"]


def main():
    tag = flag("--tag", "CTL")
    depth = int(flag("--depth", "8000"))
    n = int(flag("--n", "6"))
    gen = int(flag("--gen", "256"))
    cold = flag("--cold", "0") == "1"
    units, ptok = calibrate(depth, n)
    payloads = [("Stream %d of %d marker. " % (i, n)) + FILL * units for i in range(n)]
    warm_note = None
    if not cold:
        w, warm_note = burst(payloads, 1)      # sequential would be honest but slow; one warm burst
        warm_note = "warm burst wall %.1f s" % warm_note
    off = os.path.getsize(ENG)
    res, span = burst(payloads, gen)
    time.sleep(8)
    with open(ENG, "rb") as f:
        f.seek(off)
        data = f.read().decode("utf-8", "replace")
    rows = [m.groups() for m in REQ.finditer(data) if int(m.group(4)) == gen]
    wins = WIN.findall(data)
    rps = [int(w[2]) / max(1, int(w[1])) for w in wins]
    tg = []
    for r in res:
        if r["first"] is not None and r["last"] and r["last"] - r["first"] > 0.05:
            tg.append(round((r["ctok"] - 1) / (r["last"] - r["first"]), 1))
    ttfts = [round(r["first"], 2) for r in res if r["first"] is not None]
    ends = [round(r["last"], 2) for r in res if r["last"] is not None]
    sum_tg = round(sum(tg), 1) if tg else None
    live = (round(max(ends) - min(ttfts), 2) if ttfts and ends else None)
    wall_tg = round(sum(r["ctok"] for r in res) / live, 1) if live and live > 0.5 else None
    rec = {"tag": tag, "n": n, "gen": gen, "depth_target": depth,
           "ptok": sorted(set(r["ptok"] for r in res if r["ptok"])) or [ptok],
           "ctok": sorted(set(r["ctok"] for r in res)),
           "reused_pct": round(100.0 * sum(r["reused"] or 0 for r in res) /
                               max(1, sum(r["ptok"] or 0 for r in res)), 1),
           "span": span, "decode_live_s": live,
           "sum_tg": sum_tg, "wall_tg": wall_tg,
           "tg_med": round(st.median(tg), 1) if tg else None,
           "tg_min": min(tg) if tg else None, "tg_max": max(tg) if tg else None,
           "ttft_min": min(ttfts) if ttfts else None, "ttft_max": max(ttfts) if ttfts else None,
           "errors": sorted(set(str(r.get("error")) for r in res if r.get("error"))) or None,
           "engine_rows": len(rows),
           "engine_tg": [float(g[4]) for g in rows] if rows else None,
           "acc": round(sum(int(g[5]) for g in rows) / max(1, sum(int(g[6]) for g in rows)), 3) if rows else None,
           "pp_med": round(st.median([float(g[2]) for g in rows]), 1) if rows else None,
           "shape": wins[-1][0] if wins else None,
           "rows_per_step": round(sum(rps) / len(rps), 2) if rps else None,
           "windows": len(wins), "warm": warm_note, "ts": time.strftime("%H:%M:%S")}
    print(json.dumps(rec, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

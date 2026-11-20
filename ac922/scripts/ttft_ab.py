#!/usr/bin/env python3.11
"""Admission-policy A/B probe: a small chat request's TTFT while a cold long prompt holds the control lines.

STRATA_ADMIT_SHORT_FIRST (serve/server.py:409) orders *new* admissions so a prompt under the token
cap goes before longer ones, with STRATA_ADMIT_MAX_WAIT_S (default 30) as the anti-starvation escape.
It is a server-boot env, so each arm is its own lane boot; run this harness on each boot and compare
the medians.  It measures wall time in the admission queue, not the numbers path, so nothing here
needs a quality gate - but the answer hash is printed to show the small request was really served.

Usage (always detached, never from a bare interactive ssh per runbook 328):
  setsid nohup bash -c "python3.11 ac922/scripts/ttft_ab.py 5 240000 1.5 arm-ctl > /tmp/ttft-ctl.log 2>&1" &
  SB_URL=http://127.0.0.1:8093 ...          # dev lane: point at its port
Args: rounds big_chars lag_seconds tag
Prints numbers and hashes only - never model text.
"""
import hashlib
import json
import os
import statistics
import sys
import threading
import time
import urllib.request

URL = os.environ.get("SB_URL", "http://127.0.0.1:8091").rstrip("/")
MODEL = os.environ.get("SB_MODEL", "qwen3.8-flash-next")
CORPUS = os.environ.get("SB_CORPUS", "/data/work/corpus_h.txt")
PID_MATCH = os.environ.get("SB_PID_MATCH", "strata --serve")
SMALL = "What is the capital of France? Answer in one word."
BIG_NEW = 8          # the long request only needs to occupy the read
SMALL_NEW = 16


def api_key() -> str:
    env = os.environ.get("STRATA_API_KEY")
    if env:
        return env
    pid = os.popen(f'pgrep -f "{PID_MATCH}" | head -1').read().split()
    if not pid:
        raise SystemExit("no engine process found for the key; set STRATA_API_KEY")
    raw = open(f"/proc/{pid[0]}/environ", "rb").read().decode("utf-8", "replace").split(chr(0))
    hit = [l.split("=", 1)[1] for l in raw if l.startswith("STRATA_API_KEY=")]
    if not hit:
        raise SystemExit("engine has no STRATA_API_KEY in its environment")
    return hit[0]


KEY = api_key()


def post(payload, stream=False, timeout=900):
    req = urllib.request.Request(
        URL + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as x:
        if not stream:
            return json.loads(x.read()), round(time.time() - t0, 3), None
        ttft = None
        out = {"usage": None, "content": "", "reasoning_content": ""}
        for line in x:
            s = line.decode("utf-8", "replace").strip()
            if not s.startswith("data:"):
                continue
            body = s[5:].strip()
            if body == "[DONE]":
                break
            d = json.loads(body)
            if d.get("usage"):
                out["usage"] = d["usage"]
            for ch in d.get("choices", []):
                dl = ch.get("delta") or {}
                for f in ("content", "reasoning_content"):
                    v = dl.get(f)
                    if v:
                        if ttft is None:
                            ttft = round(time.time() - t0, 3)
                        out[f] += v
        return out, round(time.time() - t0, 3), ttft


def big(text, res, i):
    payload = {"model": MODEL, "messages": [{"role": "user", "content": text}],
               "temperature": 0, "max_tokens": BIG_NEW}
    try:
        r, wall, _ = post(payload)
        res[i] = (r["usage"]["prompt_tokens"], wall)
    except Exception as exc:                                   # noqa: BLE001
        res[i] = (-1, round(time.time() - START[0], 2))
        print("BIG-ERR", i, repr(exc)[:90], flush=True)


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    chars = int(sys.argv[2]) if len(sys.argv) > 2 else 240000
    lag = float(sys.argv[3]) if len(sys.argv) > 3 else 1.5
    tag = sys.argv[4] if len(sys.argv) > 4 else "arm"
    raw = open(CORPUS, encoding="utf-8", errors="replace").read()
    global START
    print(f"TTFT-AB tag={tag} rounds={rounds} big_chars={chars} lag={lag} url={URL}", flush=True)
    tts, tws, bws, ptrs = [], [], [], []
    for i in range(rounds):
        # a fresh slice each round keeps the long prompt cold; +1M guard against re-use within the file
        slice_ = raw[(i * chars + (hash(tag) % 50) * 1000) % max(1, len(raw) - (rounds + 1) * chars):][:chars]
        res = [None]
        START[0] = time.monotonic()
        th = threading.Thread(target=big, args=(slice_, res, 0))
        th.start()
        time.sleep(lag)
        small = {"model": MODEL, "messages": [{"role": "user", "content": SMALL}],
                 "temperature": 0, "max_tokens": SMALL_NEW, "stream": True,
                 "stream_options": {"include_usage": True}}
        try:
            out, wall, ttft = post(small, stream=True)
        except Exception as exc:                                # noqa: BLE001
            th.join()
            print("SMALL-ERR", i, repr(exc)[:90], flush=True)
            continue
        th.join()
        ptok, bwall = res[0] if res[0] else (-1, 0)
        h = hashlib.sha256((out.get("content", "") + out.get("reasoning_content", "")).encode()).hexdigest()[:10]
        ntok = (out.get("usage") or {}).get("completion_tokens", 0)
        tts.append(ttft or 0); tws.append(wall); bws.append(bwall); ptrs.append(ptok)
        print(f"R{i} big_ptok={ptok} big_wall={bwall:.1f}s small_ttft={ttft}s small_wall={wall}s "
              f"small_ctok={ntok} ans={h}", flush=True)
    if not tts:
        print("NO-SAMPLES"); return
    med = lambda xs: round(statistics.median(xs), 2)            # noqa: E731
    print("SUMMARY " + json.dumps({"tag": tag, "n": len(tts), "ttft_med": med(tts), "ttft_min": round(min(tts), 2),
                                   "ttft_max": round(max(tts), 2), "small_wall_med": med(tws),
                                   "big_wall_med": med(bws), "big_ptok_med": med(ptrs)}), flush=True)


START = [0.0]
main()

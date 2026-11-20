#!/usr/bin/env python3.11
"""Admission-policy A/B probe: a small chat request's TTFT while a cold long prompt holds the control lines.

STRATA_ADMIT_SHORT_FIRST (serve/server.py:409) orders *new* admissions so a prompt under the token
cap goes before longer ones, with STRATA_ADMIT_MAX_WAIT_S (default 30) as the anti-starvation escape.
It is a server-boot env, so each arm is its own lane boot; run this harness on each boot and compare
the medians.  It measures wall time in the admission queue, not the numbers path, so nothing here
needs a quality gate - but the answer hash is printed to show the small request was really served.

Usage (always detached, never from a bare interactive ssh per runbook 328):
  setsid nohup bash -c "python3.11 ac922/scripts/ttft_ab.py 3 120000 1.0 arm-ctl 0 4 2 > /tmp/ttft-ctl.log 2>&1" &
  SB_URL=http://127.0.0.1:8092 ...          # dev lane: point at its port
  The env must be set on the **server** process, not here: STRATA_ADMIT_SHORT_FIRST is read at
  serve boot (devlane.sh inherits the caller's env), so run one arm per lane boot.
Args: rounds big_chars lag_seconds tag base_offset n_big n_small

Why n_big > 1: short-first cannot help a lone waiter - one read is already in progress and the
policy only reorders requests that are still *waiting* for the control lines.  A burst of several
long cold prompts plus a chat probe is the shape where the policy shows up.

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
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    chars = int(sys.argv[2]) if len(sys.argv) > 2 else 120000
    lag = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
    tag = sys.argv[4] if len(sys.argv) > 4 else "arm"
    base = int(sys.argv[5]) if len(sys.argv) > 5 else 0
    nbig = int(sys.argv[6]) if len(sys.argv) > 6 else 4
    nsmall = int(sys.argv[7]) if len(sys.argv) > 7 else 2
    raw = open(CORPUS, encoding="utf-8", errors="replace").read()
    span = len(raw) - (nbig + 1) * chars
    print(f"TTFT-AB tag={tag} rounds={rounds} big_chars={chars} lag={lag} base={base} "
          f"nbig={nbig} nsmall={nsmall} url={URL}", flush=True)
    tts, tws, bws, ptrs = [], [], [], []
    for i in range(rounds):
        # a fresh slice per prompt keeps every long request cold.  base separates the two arms:
        # with DISK_PERSIST the conversation-disk tier survives a lane restart, so an arm reusing
        # the other's text would resurrect chains instead of reading them cold.  (Never Python's
        # hash() for this - it is salted per process, so an arm would not be reproducible.)
        res = [None] * nbig
        ths = []
        START[0] = time.monotonic()
        for b in range(nbig):
            start = (base + i * nbig * chars + b * chars) % max(1, span)
            t = threading.Thread(target=big, args=(raw[start:start + chars], res, b))
            t.start(); ths.append(t)
            time.sleep(0.3)                     # a burst, not a single instant
        time.sleep(max(0.0, lag - 0.3 * (nbig - 1)))
        probes = []
        for k in range(nsmall):
            small = {"model": MODEL, "messages": [{"role": "user", "content": SMALL}],
                     "temperature": 0, "max_tokens": SMALL_NEW, "stream": True,
                     "stream_options": {"include_usage": True}}
            try:
                out, wall, ttft = post(small, stream=True)
            except Exception as exc:                          # noqa: BLE001
                print("SMALL-ERR", i, k, repr(exc)[:90], flush=True)
                continue
            h = hashlib.sha256((out.get("content", "") + out.get("reasoning_content", "")).encode()).hexdigest()[:10]
            ntok = (out.get("usage") or {}).get("completion_tokens", 0)
            probes.append((ttft or 0.0, wall, h, ntok))
            tts.append(ttft or 0.0); tws.append(wall)
        for t in ths:
            t.join()
        goods = [r for r in res if r and r[0] > 0]
        bws.extend(r[1] for r in goods); ptrs.extend(r[0] for r in goods)
        print(f"R{i} big_ok={len(goods)}/{nbig} big_ptok={[r[0] for r in goods]} "
              f"big_wall={[r[1] for r in goods]} probes="
              + json.dumps([{"ttft": p[0], "wall": p[1], "ctok": p[3], "ans": p[2]} for p in probes]), flush=True)
    if not tts:
        print("NO-SAMPLES"); return
    def med(xs):
        return round(statistics.median(xs), 2) if xs else None
    print("SUMMARY " + json.dumps({"tag": tag, "n_probes": len(tts), "ttft_med": med(tts),
                                   "ttft_min": round(min(tts), 2), "ttft_max": round(max(tts), 2),
                                   "small_wall_med": med(tws), "big_wall_med": med(bws),
                                   "big_ptok_med": med(ptrs)}), flush=True)


START = [0.0]
main()

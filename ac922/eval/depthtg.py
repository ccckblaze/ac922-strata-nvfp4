#!/usr/bin/env python3.11
"""Targeted-depth decode probe: build a prompt at DT_DEPTH, then N decode rounds of DT_GEN tokens.

sbench.py `long` walks a fixed ladder; the boot-level arms (an env flag that only engages past the
register kernel's reach) need ONE depth read repeatedly, so a median over rounds is possible.
Warm the prefix once, ungraded (cold prefill and cached replay are different paths), then measure.

Prints numbers and 12-hex reply digests only -- never model text.

env: SB_URL, SB_ENG, STRATA_API_KEY, SB_MODEL, DT_DEPTH (default 96000), DT_N (default 5), DT_GEN (default 64).
"""
import json, os, re, statistics, time, hashlib, urllib.request, urllib.error
URL = os.environ.get("SB_URL", "http://127.0.0.1:8092")
ENG = os.environ.get("SB_ENG", "/data/champion/strata-nvfp4-dev-engine.log")
MODEL = os.environ.get("SB_MODEL", "qwen3.8-flash-next")
DEPTH = int(os.environ.get("DT_DEPTH", "96000"))
N = int(os.environ.get("DT_N", "5"))
GEN = int(os.environ.get("DT_GEN", "64"))
KEY = os.environ.get("STRATA_API_KEY", "")
FILL = "Reimagine a lighthouse keeper logging nightly waves and distant ships; "
OFF = [os.path.getsize(ENG)] if os.path.exists(ENG) else [0]


def post(payload, timeout=7200):
    hd = {"Content-Type": "application/json"}
    if KEY:
        hd["Authorization"] = "Bearer " + KEY
    r = urllib.request.Request(URL + "/v1/chat/completions", data=json.dumps(payload).encode(), headers=hd)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            return json.loads(x.read())
    except urllib.error.HTTPError as e:
        raise SystemExit("DEPTH-ERROR http-%d: depth %d may exceed the lane's window" % (e.code, DEPTH))


def ask(text, n):
    t0 = time.time()
    r = post({"model": MODEL, "messages": [{"role": "user", "content": text}], "temperature": 0, "max_tokens": n})
    msg = r["choices"][0]["message"]
    with open(ENG, "rb") as f:
        end = f.seek(0, 2)
        f.seek(OFF[0]); news = f.read().decode("utf-8", "replace"); OFF[0] = end
    line = ""
    for ln in news.splitlines():
        if "strata serve: prompt" in ln:
            line = ln
    m = re.findall(r"([\d.]+) tok/s", line)
    acc = re.search(r"drafts accepted (\d+) of (\d+)", line)
    return {"wall": round(time.time() - t0, 2),
            "ptok": r.get("usage", {}).get("prompt_tokens"),
            "ctok": r.get("usage", {}).get("completion_tokens"),
            "pp": float(m[0]) if m else None,
            "tg": float(m[1]) if len(m) > 1 else None,
            "acc": (int(acc.group(1)), int(acc.group(2))) if acc else None,
            "hash": hashlib.sha256(json.dumps(msg, sort_keys=True).encode()).hexdigest()[:12]}


per = post({"model": MODEL, "messages": [{"role": "user", "content": FILL * 20}], "temperature": 0,
            "max_tokens": 1})["usage"]["prompt_tokens"] / 20.0
text = FILL * max(1, int((DEPTH - 40) / per))
w = ask(text, 8)
print("warm ptok=%s pp=%s" % (w["ptok"], w["pp"]), flush=True)
rows = []
for i in range(N):
    r = ask(text, GEN)
    rows.append(r)
    print("R%d tg=%s acc=%s ctok=%s hash=%s" % (i + 1, r["tg"], r["acc"], r["ctok"], r["hash"]), flush=True)
tgs = sorted(x["tg"] for x in rows if x["tg"])
hashes = sorted({x["hash"] for x in rows})
print("DEPTHTG", json.dumps({"depth_target": DEPTH, "ptok": w["ptok"], "n": N, "gen": GEN,
                            "tg_med": round(statistics.median(tgs), 2) if tgs else None,
                            "tg_min": tgs[0] if tgs else None, "tg_max": tgs[-1] if tgs else None,
                            "hashes": hashes}), flush=True)

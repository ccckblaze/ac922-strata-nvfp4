#!/usr/bin/env python3.11
"""Solo window-time probe: one deterministic short request, numbers only.

Purpose: separate co-tenant interference from engine configuration.  Prints no model content.
Usage: probe_solo.py [--tokens N] [--depth-tokens D]
"""
import argparse, json, os, random, re, statistics as S, subprocess, time, urllib.request

ENGINE = os.environ.get("ENGINE_LOG", "/data/champion/strata-nvfp4-engine.log")
URL = os.environ.get("URL", "http://127.0.0.1:8091/v1/chat/completions")
KEY = os.environ.get("STRATA_API_KEY", "")
WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india kilo lima mike november "
         "oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu").split()
DT = re.compile(r"decode timing: ([\d.]+) windows, avg T ([\d.]+), ([\d.]+) tokens/window, "
                r"([\d.]+) ms/window")


def prompt_text(target_tokens):
    rnd = random.Random(20261123)
    chars = int(target_tokens / 0.60)          # words corpus ~0.6 tokens/char on this pack
    out = []
    n = 0
    while n < chars:
        w = rnd.choice(WORDS)
        out.append(w)
        n += len(w) + 1
    return " ".join(out)[:chars]


def size():
    return os.path.getsize(ENGINE)


def new_lines(a):
    with open(ENGINE, "rb") as f:
        f.seek(a)
        return f.read().decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokens", type=int, default=150)
    ap.add_argument("--depth-tokens", type=int, default=16384)
    ap.add_argument("--tag", default="probe")
    a = ap.parse_args()
    text = prompt_text(a.depth_tokens)
    body = {"model": "strata", "messages": [{"role": "user", "content": text}],
            "max_tokens": a.tokens, "temperature": 0, "stream": True,
            "sampling": {"strata_checkpoint": False}}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + KEY})
    off = size()
    t0 = time.time()
    tok = 0
    with urllib.request.urlopen(req, timeout=600) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            pay = line[5:].strip()
            if pay == "[DONE]":
                break
            try:
                d = json.loads(pay)
            except Exception:
                continue
            for c in d.get("choices", []):
                tok += 1 if (c.get("delta") or {}).get("content") else 0
    wall = time.time() - t0
    blob = new_lines(off)
    ms = [float(m.group(4)) for m in DT.finditer(blob)]
    tpw = [float(m.group(3)) for m in DT.finditer(blob)]
    ms_ok = sorted(v for v in ms if 0 < v < 5000)
    print(json.dumps({"tag": a.tag, "tokens": tok, "wall_s": round(wall, 2),
                      "tg": round(tok / wall, 2) if wall > 0 else None,
                      "dt_lines": len(ms),
                      "mswin_p50": round(S.median(ms_ok), 2) if ms_ok else None,
                      "mswin_p90": round(ms_ok[int(0.9 * (len(ms_ok) - 1))], 2) if len(ms_ok) > 3 else None,
                      "mswin_min": round(min(ms_ok), 2) if ms_ok else None,
                      "tpw_med": round(S.median([v for v in tpw if v > 0]), 2) if tpw else None}))


main()

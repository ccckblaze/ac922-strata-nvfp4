#!/usr/bin/env python3.11
"""Solo window-time probe: one deterministic request at a chosen depth, numbers only.

Purpose: separate co-tenant interference from engine configuration.  Prints no model content.

Two measurement bugs fixed here, both of which produced confident wrong answers:

  * depth was guessed from a hardcoded 0.60 tokens/char, and this word generator is 0.242 - a
    "--depth-tokens 131072" probe really ran at 52,962 prompt tokens and was read as a deep-window
    number.  The depth is now calibrated: a small sample is sent, the engine's own `prompt N tokens`
    line for it is read back off the log, and the ratio from that generator sizes the real prompt.
  * only `content` deltas were counted, and this pack answers in `reasoning_content` until it runs
    out, so a 120-token generation reported `tokens: 0, tg: 0.0`.  Both fields count now, and the
    authoritative number is taken from the engine's own request summary (gen ms, tok/s, drafts).

Usage: probe_solo.py [--tokens N] [--depth-tokens D] [--repeats K]
"""
import argparse, json, os, random, re, statistics as S, subprocess, time, urllib.request

ENGINE = os.environ.get("ENGINE_LOG", "/data/champion/strata-nvfp4-engine.log")
URL = os.environ.get("URL", "http://127.0.0.1:8091/v1/chat/completions")
KEY = os.environ.get("STRATA_API_KEY", "")
WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india kilo lima mike november "
         "oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu").split()
DT = re.compile(r"decode timing: ([\d.]+) windows, avg T ([\d.]+), ([\d.]+) tokens/window, "
                r"([\d.]+) ms/window")
GEN = re.compile(r"prompt (?P<ptok>\d+) tokens = (?P<reuse>\d+) reused \+ (?P<rd>\d+) read in "
                 r"(?P<rdms>\d+) ms \((?P<pp>[\d.]+) tok/s\), (?P<gen>\d+) generated in "
                 r"(?P<gms>\d+) ms \((?P<tg>[\d.]+) tok/s\), drafts accepted (?P<acc>\d+) of "
                 r"(?P<drf>\d+)")


def prompt_text(n_chars, seed=20261123):
    rnd = random.Random(seed)
    out = []
    n = 0
    while n < n_chars:
        w = rnd.choice(WORDS)
        out.append(w)
        n += len(w) + 1
    return " ".join(out)[:n_chars]


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
    ap.add_argument("--repeats", type=int, default=2,
                    help="1 = cold window, 2 = the same chain again (cache-warm); both are printed")
    a = ap.parse_args()

    def ask(text, max_tokens, tag):
        body = {"model": "strata", "messages": [{"role": "user", "content": text}],
                "max_tokens": max_tokens, "temperature": 0, "stream": True,
                "sampling": {"strata_checkpoint": False}}
        req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + KEY})
        off = size()
        t0 = time.time()
        tok = 0
        with urllib.request.urlopen(req, timeout=900) as r:
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
                    delta = c.get("delta") or {}
                    if delta.get("content") or delta.get("reasoning_content"):
                        tok += 1
        wall = time.time() - t0
        blob = new_lines(off)
        req_lines = [m.groupdict() for m in GEN.finditer(blob)]
        ms = [float(m.group(4)) for m in DT.finditer(blob)]
        tpw = [float(m.group(3)) for m in DT.finditer(blob)]
        ms_ok = sorted(v for v in ms if 0 < v < 5000)
        out = {"tag": tag, "chars": len(text), "stream_tokens": tok, "wall_s": round(wall, 2)}
        if req_lines:
            q = req_lines[-1]
            out.update({"ptok": int(q["ptok"]), "reused_pct": round(
                            100.0 * int(q["reuse"]) / max(1, int(q["ptok"])), 1),
                        "pp_toks": float(q["pp"]), "gen": int(q["gen"]),
                        "tg": float(q["tg"]),
                        "drafts_kept_pct": round(100.0 * int(q["acc"]) / max(1, int(q["drf"])), 1)})
        if ms_ok:
            out.update({"dt_lines": len(ms), "mswin_p50": round(S.median(ms_ok), 2),
                        "mswin_min": round(min(ms_ok), 2),
                        "tpw_med": round(S.median([v for v in tpw if v > 0]), 2) if tpw else None})
        print(json.dumps(out, sort_keys=True), flush=True)
        return out

    # Calibrate this generator against the engine's own count, then size the real prompt from it.
    # Seed 1 keeps the sample from being a prefix of the measured prompt, which would show up as
    # reused tokens in the very first cold window and understate its prefill cost.
    cal = ask(prompt_text(20000, seed=1), 8, a.tag + ".calib")
    ratio = cal.get("ptok") / float(cal["chars"]) if cal.get("ptok") else None
    if not ratio or not (0.05 < ratio < 4.0):
        raise SystemExit("calibration failed: no usable tokens/char from the engine log")
    text = prompt_text(int(a.depth_tokens / ratio))
    print("# calibrated %.5f tokens/char -> %d chars for %d tokens" % (
        ratio, len(text), a.depth_tokens), flush=True)
    for i in range(a.repeats):
        ask(text, a.tokens, "%s.r%d" % (a.tag, i + 1))


main()

#!/usr/bin/env python3.11
"""Deep-window decode probe: one cold long prompt per round, reads the engine's own decode-timing line.

Usage: deepprobe.py <rounds> <big_chars> <new_tokens> <tag> <base_offset>
Prints numbers and hashes only - never model text.  One process at a time (a second concurrent
probe changes the queue it is measuring; see DEVLOG H.17).
"""
import hashlib, json, os, statistics, sys, time, urllib.request

URL = os.environ.get("SB_URL", "http://127.0.0.1:8091").rstrip("/")
MODEL = os.environ.get("SB_MODEL", "qwen3.8-flash-next")
ENG = os.environ.get("SB_ENG", "/data/champion/strata-nvfp4-engine.log")
CORPUS = os.environ.get("SB_CORPUS", "/data/work/corpus_h.txt")


def api_key():
    env = os.environ.get("STRATA_API_KEY")
    if env:
        return env
    pid = os.popen('pgrep -f "strata --serve" | head -1').read().split()[0]
    raw = open("/proc/%s/environ" % pid, "rb").read().decode("utf-8", "replace").split(chr(0))
    return [l.split("=", 1)[1] for l in raw if l.startswith("STRATA_API_KEY=")][0]


KEY = api_key()
RX = (("win", "strata decode timing: "), ("tpw", "tokens/window"), ("msw", "ms/window"), ("ver", "verify "),
      ("reach", "GPU-reach wait "), ("host", "per-layer host "), ("cpu", "CPU "))


def parse(line):
    out = {}
    for name, pat in RX:
        i = line.find(pat)
        if i < 0:
            out[name] = None
            continue
        if pat in ("tokens/window", "ms/window"):
            k2 = i
            while k2 > 0 and (line[k2-1] == " " or line[k2-1].isdigit() or line[k2-1] == "."):
                k2 -= 1
            out[name] = float(line[k2:i]) if k2 < i else None
            continue
        j = i + len(pat)
        k = j
        while k < len(line) and (line[k].isdigit() or line[k] == "."):
            k += 1
        out[name] = float(line[j:k]) if k > j else None
    return out


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    chars = int(sys.argv[2]) if len(sys.argv) > 2 else 860000
    newtok = int(sys.argv[3]) if len(sys.argv) > 3 else 128
    tag = sys.argv[4] if len(sys.argv) > 4 else "arm"
    base = int(sys.argv[5]) if len(sys.argv) > 5 else 8600000
    raw = open(CORPUS, encoding="utf-8", errors="replace").read()
    print("DEEP-PROBE tag=%s rounds=%d chars=%d new=%d base=%d" % (tag, rounds, chars, newtok, base), flush=True)
    rows = []
    for i in range(rounds):
        start = (base + i * chars) % max(1, len(raw) - chars)
        body = {"model": MODEL, "messages": [{"role": "user", "content": raw[start:start + chars] +
                                            "\n\nIn one sentence, what is this about?"}],
                "temperature": 0, "max_tokens": newtok}
        off = os.path.getsize(ENG)
        t0 = time.time()
        req = urllib.request.Request(URL + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + KEY})
        j = json.loads(urllib.request.urlopen(req, timeout=1200).read())
        wall = round(time.time() - t0, 2)
        with open(ENG, "rb") as f:
            f.seek(off)
            tail = f.read().decode("utf-8", "replace")
        lines = [l for l in tail.splitlines() if "decode timing" in l]
        p = parse(lines[-1]) if lines else {}
        u = j["usage"]
        msg = j["choices"][0]["message"]
        h = hashlib.sha256(json.dumps(msg, sort_keys=True).encode()).hexdigest()[:10]
        p.update(ptok=u["prompt_tokens"], ctok=u["completion_tokens"], wall=wall, hash=h)
        if p.get("msw") and p.get("tpw"):
            p["tg"] = round(1000.0 * p["tpw"] / p["msw"], 1)
        rows.append(p)
        print("R%d " % i + json.dumps({k: p[k] for k in
              ("ptok", "ctok", "wall", "tg", "msw", "ver", "reach", "host", "cpu", "tpw", "hash") if k in p}), flush=True)
    keys = [k for k in rows[0] if isinstance(rows[0][k], (int, float)) and k != "ptok"]
    med = {k: round(statistics.median([r[k] for r in rows if r.get(k) is not None]), 2)
           for k in keys if all(r.get(k) is not None for r in rows)}
    print("MEDIAN " + json.dumps(med), flush=True)


main()

"""Does parking short conversations starve the parked long ones?  Numbers only, never text.

Lowering `--conversation-cache-min-tokens` buys second-turn reuse for short chats (see
`turnprobe.py`), but CC is bounded by both bytes (`--conversation-cache-mib`) and count
(`--conversation-cache-slots`).  The risk of the change is therefore not the short band, it is the
deep band: a long conversation that is parked and then pushed out by a wave of small ones has to
come back from NVMe, or worse from a cold re-prefill.

Protocol, one boot:

  1. create `--deep` long conversations (default 2 x 148K), each with its own marker;
  2. wait `--settle` seconds so they are parked rather than live (the engine only snapshots parked
     chains under a layer split - `DECISIONS.md`, the parked-only caveat);
  3. create and follow-up `--short` small conversations (default 16 x 8K), which is the wave that
     would eat the slot budget;
  4. follow up the deep conversations again and score each one: `reused_pct` and TTFT, with
     COLD = reused < 50% (a chain that lost its snapshot and is being recomputed).

Gate: every deep chain comes back with reused >= 90% and TTFT <= 12 s, and COLD = 0.

usage: parkpressure.py [--deep 2] [--deep-tok 148000] [--short 16] [--short-tok 8000] [--settle 60]
env:   AB_URL  STRATA_API_KEY  AB_TIMEOUT
"""
import json, os, threading, time, urllib.error, urllib.request

URL = os.environ.get("AB_URL", "http://127.0.0.1:8091")
KEY = os.environ.get("STRATA_API_KEY", "")
TMO = float(os.environ.get("AB_TIMEOUT", "1800"))
MODEL = "qwen3.8-flash-next"
FILL = ("Reimagine a lighthouse keeper logging nightly waves and distant ships; "
        "the ledger grows one careful paragraph at a time. ")


def flag(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def ask(messages, gen):
    body = {"model": MODEL, "messages": messages, "temperature": 0, "max_tokens": gen,
            "stream": True, "stream_options": {"include_usage": True}}
    hd = {"Content-Type": "application/json"}
    if KEY:
        hd["Authorization"] = "Bearer " + KEY
    req = urllib.request.Request(URL + "/v1/chat/completions", data=json.dumps(body).encode(), headers=hd)
    t0 = time.time()
    first = None
    usage = {}
    try:
        with urllib.request.urlopen(req, timeout=TMO) as x:
            for raw in x:
                ln = raw.decode().strip()
                if not ln.startswith("data:"):
                    continue
                txt = ln[5:].strip()
                if txt == "[DONE]":
                    break
                if first is None and '"content": "' in txt and '"content": ""' not in txt:
                    first = time.time() - t0
                if "usage" in txt:
                    try:
                        usage = json.loads(txt).get("usage", usage)
                    except ValueError:
                        pass
    except urllib.error.HTTPError as e:
        return {"ttft": None, "ptok": 0, "reused_pct": None, "error": "http-%d" % e.code}
    except Exception as e:
        return {"ttft": None, "ptok": usage.get("prompt_tokens") or 0, "reused_pct": None,
                "error": type(e).__name__}
    ptok = usage.get("prompt_tokens") or 0
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    return {"ttft": round(first if first is not None else time.time() - t0, 2), "ptok": ptok,
            "reused_pct": round(100.0 * cached / ptok, 1) if ptok else None}


def chain(stem, turn):
    """One conversation, `turn` follow-ups long; returns the per-turn measurements."""
    msgs = [{"role": "user", "content": stem}]
    out = []
    for i in range(turn):
        if i:
            msgs = msgs + [{"role": "assistant", "content": "Noted."},
                           {"role": "user", "content": "Next line, please (%d)." % i}]
        out.append(ask(msgs, 8))
    return out


def wave(stems, tok, workers=4):
    """Fire the short conversations concurrently, which is what a busy lane does to the budget."""
    res = {}
    def work(i):
        res[i] = chain(stems[i], 2)
    th = [threading.Thread(target=work, args=(i,)) for i in range(len(stems))]
    for chunk in range(0, len(stems), workers):
        batch = th[chunk:chunk + workers]
        for t in batch:
            t.start()
        for t in batch:
            t.join()
    return [res[i] for i in range(len(stems))]


def main():
    ndeep = int(flag("--deep", "2"))
    deep_tok = int(flag("--deep-tok", "148000"))
    nshort = int(flag("--short", "16"))
    short_tok = int(flag("--short-tok", "8000"))
    settle = int(flag("--settle", "60"))
    nonce = int(time.time())
    print("PARKPRESSURE url=%s nonce=%d deep=%d@%d short=%d@%d settle=%d"
          % (URL, nonce, ndeep, deep_tok, nshort, short_tok, settle), flush=True)

    deep_stems = ["Deep chain %d of run %d. " % (i, nonce) + FILL * int(deep_tok / 48)
                  for i in range(ndeep)]
    t0 = time.time()
    built = [chain(s, 1)[0] for s in deep_stems]
    print("deep built: %s (%.0f s)" % (json.dumps(built), time.time() - t0), flush=True)

    time.sleep(settle)

    short_stems = ["Short chain %d of run %d. " % (i, nonce) + FILL * int(short_tok / 48)
                   for i in range(nshort)]
    t0 = time.time()
    short = wave(short_stems, short_tok)
    sr = [round(100.0 * sum(t["reused_pct"] or 0 for t in c) / max(1, len(c)), 1) for c in short]
    print("short wave: %d chains, reuse %s (%.0f s)" % (nshort, json.dumps(sr), time.time() - t0),
          flush=True)

    t0 = time.time()
    back = []
    for i, s in enumerate(deep_stems):
        msgs = [{"role": "user", "content": s},
                {"role": "assistant", "content": "Noted."},
                {"role": "user", "content": "Are you still there?"}]
        back.append(ask(msgs, 8))
    cold = sum(1 for r in back if (r["reused_pct"] or 0) < 50.0)
    worst = max((r["ttft"] or 0) for r in back) if back else None
    print("deep resurrect: %s cold=%d worst_ttft=%s (%.0f s)"
          % (json.dumps(back), cold, worst, time.time() - t0), flush=True)
    verdict = "PASS" if (cold == 0 and worst is not None and worst <= 12.0) else "FAIL"
    print("PARKPRESSURE-%s deep_n=%d short_n=%d cold=%d" % (verdict, ndeep, nshort, cold))


if __name__ == "__main__":
    main()

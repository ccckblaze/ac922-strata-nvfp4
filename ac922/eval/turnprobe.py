"""Where does a second turn stop paying for the first one?  Numbers only, never text.

`--conversation-cache-min-tokens` decides which conversations are parked at all, so it decides
whether a follow-up turn reuses its own history or re-prefills it.  The aggregate benches cannot see
that cleanly (their warm burst is the same-process prefix), so this probe measures the thing the user
actually experiences, per depth, on one boot:

  cold    a brand-new conversation of `depth` tokens, first turn
  repeat  the identical request again (same conversation, one turn later)
  follow  one short user message appended to that history (the real chat shape)

For each it reports TTFT and the share of `prompt_tokens` the engine says it already had
(`usage.prompt_tokens_details.cached_tokens`).  A depth whose `repeat`/`follow` reuse falls to ~0 is
a depth whose turns are re-computed from scratch, whatever the parking budget looks like on paper.

usage: turnprobe.py [--depths 4000,8000,16000,40000,80000] [--gen 8]
env:   AB_URL  STRATA_API_KEY  AB_TIMEOUT
"""
import json, os, sys, time, urllib.error, urllib.request

URL = os.environ.get("AB_URL", "http://127.0.0.1:8091")
KEY = os.environ.get("STRATA_API_KEY", "")
TMO = float(os.environ.get("AB_TIMEOUT", "1200"))
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
        if e.code in (401, 403):
            raise SystemExit("TP-ERROR auth-%d: export STRATA_API_KEY first" % e.code)
        raise SystemExit("TP-ERROR http-%d" % e.code)
    ptok = usage.get("prompt_tokens") or 0
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    if first is None:
        first = time.time() - t0
    return {"ttft": round(first, 2), "ptok": ptok,
            "reused_pct": round(100.0 * cached / ptok, 1) if ptok else None}


def main():
    depths = [int(x) for x in flag("--depths", "4000,8000,16000,40000,80000").split(",")]
    gen = int(flag("--gen", "8"))
    nonce = int(time.time())
    print("TURNPROBE url=%s gen=%d nonce=%d" % (URL, gen, nonce))
    for i, target in enumerate(depths):
        units = max(1, int(target / 48))
        stem = "Conversation %d of run %d. " % (i, nonce) + FILL * units
        cold = ask([{"role": "user", "content": stem}], gen)
        repeat = ask([{"role": "user", "content": stem}], gen)
        follow = ask([{"role": "user", "content": stem},
                      {"role": "assistant", "content": "Noted."},
                      {"role": "user", "content": "One line more, please."}], gen)
        rec = {"depth": target, "cold": cold, "repeat": repeat, "follow": follow}
        print(json.dumps(rec, sort_keys=True), flush=True)
    print("TURNPROBE-DONE")


if __name__ == "__main__":
    main()

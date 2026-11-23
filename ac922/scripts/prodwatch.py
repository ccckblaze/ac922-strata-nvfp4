#!/usr/bin/env python3.11
"""Production traffic profiler (read-only).

The engine's request line has no timestamp, but it reports its own service time, so a line observed at
wall clock T lets us reconstruct the interval [T - (prefill_ms + gen_ms)/1000, T] during which the
request definitely held the lane.  Overlapping those intervals gives a lower bound on in-flight
concurrency, which is what makes a deep-window throughput table under live traffic interpretable at all.

Numbers only: no prompt text, no completion text, no template tokens.
Usage: prodwatch.py --seconds N [--out FILE]
"""
import argparse, bisect, json, os, re, statistics as S, sys, time

ENGINE = os.environ.get("ENGINE_LOG", "/data/champion/strata-nvfp4-engine.log")
REQ = re.compile(
    r"strata serve: prompt (?P<ptok>[\d,]+) tokens = (?P<reuse>[\d,]+) reused \+ (?P<read>[\d,]+) read in "
    r"(?P<pms>[\d,.]+) ms \([^)]*\), (?P<gen>[\d,]+) generated in (?P<gms>[\d,.]+) ms"
    r"(?:, drafts accepted (?P<dacc>[\d,]+) of (?P<ddraft>[\d,]+))?")
CENS = re.compile(r"batch \(pipelined, (\d+) groups of (\d+)\): ([\d,]+) group-steps, ([\d,]+) rows in "
                  r"([\d,.]+) ms = ([\d,.]+) rows/s(?:[^\n]*?\(([\d,]+) of ([\d,]+) MTP proposals kept\))?")
DT = re.compile(r"decode timing: ([\d,.]+) windows, avg T ([\d,.]+), ([\d,.]+) tokens/window, "
                r"([\d,.]+) ms/window")


def num(s, default=0.0):
    if s is None:
        return default
    return float(s.replace(",", ""))


def pct(v, q):
    if not v:
        return None
    v = sorted(v)
    return round(v[min(len(v) - 1, int(q * (len(v) - 1)))], 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=900)
    ap.add_argument("--out", default="/data/work/prodwatch.jsonl")
    a = ap.parse_args()
    f = open(ENGINE, "rb")
    f.seek(os.path.getsize(ENGINE))
    out = open(a.out, "a")
    spans = []           # (start, end, idx)
    reqs = []
    cens = []
    t_end = time.time() + a.seconds
    while time.time() < t_end:
        blob = f.read()
        if not blob:
            time.sleep(0.5)
            continue
        text = blob.decode("utf-8", "replace")
        now = time.time()
        for m in REQ.finditer(text):
            d = m.groupdict()
            ptok, reuse = num(d["ptok"]), num(d["reuse"])
            pms, gen, gms = num(d["pms"]), num(d["gen"]), num(d["gms"])
            dur = (pms + gms) / 1000.0
            idx = len(reqs)
            spans.append((now - dur, now, idx))
            tg = 1000.0 * gen / gms if gen >= 20 and gms > 0 else None
            reqs.append({"t_done": round(now, 2), "dur_s": round(dur, 2), "ptok": int(ptok),
                         "reuse_frac": round(reuse / ptok, 3) if ptok else 0.0,
                         "gen": int(gen), "pp_tok_s": round(1000.0 * ptok / pms, 1) if pms else None,
                         "tg": round(tg, 2) if tg else None,
                         "dacc": (num(d["dacc"]) / num(d["ddraft"])) if d.get("ddraft") and num(d["ddraft"]) else None})
        for m in CENS.finditer(text):
            g, s, st, rw, ms, rate = (num(m.group(1)), num(m.group(2)), num(m.group(3)), num(m.group(4)),
                                      num(m.group(5)), num(m.group(6)))
            kept = num(m.group(7)) / num(m.group(8)) if m.group(7) and num(m.group(8)) else None
            cens.append({"t": round(now, 2), "shape": "%d/%d" % (g, s),
                         "step_ms": round(ms / st, 1) if st else None,
                         "rows_step": round(rw / st, 2) if st else None, "rows_s": rate, "keep": kept})
        for m in DT.finditer(text):
            w, tpw, mswin = num(m.group(1)), num(m.group(3)), num(m.group(4))
            if 0 < mswin < 20000:
                cens.append({"t": round(now, 2), "dt_windows": int(w), "tpw": tpw, "mswin": mswin})
    f.close()
    # concurrency per request: how many other reconstructed intervals overlap its own
    conc = {}
    for st, en, idx in spans:
        n = 0
        for st2, en2, idx2 in spans:
            if idx2 != idx and st2 < en and en2 > st:
                n += 1
        conc[idx] = n
    for idx, r in enumerate(reqs):
        r["concurrent"] = conc.get(idx, 0)
        out.write(json.dumps(r) + "\n")
    out.close()

    def bucket(p):
        if p < 8000: return "<8K"
        if p < 32000: return "8-32K"
        if p < 100000: return "32-100K"
        if p < 200000: return "100-200K"
        return "200K+"

    print("\nrequests=%d  census=%d  window=%ds" % (len(reqs), len(cens), a.seconds))
    print("%-10s %6s %8s %8s %8s %8s %8s %8s" % ("depth", "n", "tg_p50", "tg_p90", "reuse_p50",
                                                  "pp_p50", "conc_p50", "conc_p90"))
    for b in ["<8K", "8-32K", "32-100K", "100-200K", "200K+"]:
        sel = [r for r in reqs if bucket(r["ptok"]) == b]
        if not sel:
            continue
        print("%-10s %6d %8s %8s %8.2f %8s %8.1f %8.1f" % (
            b, len(sel), pct([r["tg"] for r in sel if r["tg"]], .5), pct([r["tg"] for r in sel if r["tg"]], .9),
            S.median([r["reuse_frac"] for r in sel]), pct([r["pp_tok_s"] for r in sel if r["ptok"] > 4000], .5),
            S.median([r["concurrent"] for r in sel]), pct([r["concurrent"] for r in sel], .9)))
    print("\nby in-flight concurrency (deep requests, ptok>100K, gen>=20):")
    for c in [0, 1, 2, 3, 4]:
        sel = [r for r in reqs if r["ptok"] > 100000 and r["tg"] and r["concurrent"] == c]
        if sel:
            print("  concurrent=%d  n=%-4d tg p50=%.1f p90=%.1f" % (c, len(sel), pct([r["tg"] for r in sel], .5),
                                                                    pct([r["tg"] for r in sel], .9)))
    print("\nring census this window: shape=%s  step_ms p50=%s  rows/step p50=%s  rows/s p50=%s  keep p50=%s" % (
        sorted({c.get("shape") for c in cens if c.get("shape")}), pct([c["step_ms"] for c in cens if c.get("step_ms")], .5),
        pct([c["rows_step"] for c in cens if c.get("rows_step")], .5), pct([c["rows_s"] for c in cens if c.get("rows_s")], .5),
        pct([c["keep"] for c in cens if c.get("keep")], .5)))
    solo = [c["mswin"] for c in cens if c.get("mswin") and c.get("dt_windows", 1) == 1]
    if solo:
        print("solo-path windows: n=%d mswin p50=%.1f p90=%.1f" % (len(solo), pct(solo, .5), pct(solo, .9)))


main()

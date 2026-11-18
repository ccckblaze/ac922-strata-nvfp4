#!/usr/bin/env python3.11
"""Lane log audit: judge the production lane against its anchors using the engine's own telemetry.

Run it every time work switches back from the production lane.  Read-only, no GPU, no request.  A
green `/health` cannot answer any of the three questions this does:

  RATES      did decode keep its adopted speed by window band, and cold prefill likewise
  MECHANISMS batch rows per group-step, MTP acceptance, expert/KV tier hits, zero expert reads from
             storage, conversation-cache park/restore balance and eviction shape
  STRUCTURE  engine respawn, stalls, remote NUMA reads, residency saturation, and the inversion where
             a short window runs SLOWER than a deep one (that pattern is a defect, never noise)

Anchors are the adopted ship state in `PERFORMANCE.md` section 3, split by card count.  Decode is
compared against a band's p90 rather than its median because a lane serving concurrent traffic
legitimately runs each stream slower; a collapsed p90 is not legitimate.

Prints integers, ratios, band labels and PASS/WARN/FAIL -- never model or prompt text.

env: ENG engine log (default /data/champion/strata-nvfp4-engine.log)
     CARDS 4|6 lane card count (default 4)
     SINCE boot|delta|<lines> scope: the current boot (default), everything since the last
           audit watermark, or N trailing lines
     WM   watermark file for SINCE=delta (default /data/work/laneaudit.watermark).  `delta` audits
          only the traffic nobody has audited yet and then moves the watermark to end-of-log, so a
          switch back from production reads the new traffic instead of re-reading history.  Rate
          bands with too few samples are skipped, so an audit right after a quiet stretch prints
          fewer lines rather than inventing a verdict.
     GEN_MIN ignore decode samples with fewer generated tokens than this (default 32)

Sample floors: the boot scope judges a decode band at n>=3 and cold prefill at n>=2; a narrower
scope (delta or trailing lines) needs 6 and 4 and prints SKIP below that, because a handful of
requests sharing the lane with live concurrency is not a band rate.
"""
import os, re, statistics

ENG = os.environ.get("ENG", "/data/champion/strata-nvfp4-engine.log")
CARDS = int(os.environ.get("CARDS", "4"))
SINCE = os.environ.get("SINCE", "boot")
GEN_MIN = int(os.environ.get("GEN_MIN", "32"))
WM = os.environ.get("WM", "/data/work/laneaudit.watermark")

BANDS = [(0, 16000, "b1<16K"), (16000, 80000, "b2 16-80K"), (80000, 165000, "b3 80-165K"),
         (165000, 240000, "b4 165-240K"), (240000, 1 << 40, "b5>=240K")]
ANCHOR = {4: {"tg": [69.9, 71.5, 72.0, 66.0, 66.8], "pp": [2075.6, 3977.3, 3964.3, 3817.2, 3562.8]},
          6: {"tg": [82.1, 76.4, 76.1, 77.9, 81.1], "pp": [2498.8, 6104.3, 6047.4, 5830.7, 5441.2]}}

R_PROMPT = re.compile(r"strata serve: prompt (\d+) tokens = (\d+) reused \+ (\d+) read in [\d.]+ ms "
                      r"\(([\d.]+|NA) tok/s\), (\d+) generated in [\d.]+ ms \(([\d.]+|NA) tok/s\), "
                      r"drafts accepted (\d+) of (\d+)")
R_RES = re.compile(r"resident RAM: ([\d.]+) GiB of experts in RAM, (\d+) exchanged with the VRAM tier, "
                   r"(\d+) blob reads")
R_HIT = re.compile(r"decode expert cache hit rate: ([\d.]+)% \((\d+) hits / (\d+) lookups\)")
R_KV = re.compile(r"KV streaming: ([\d.]+)% of \d+ block reads hit VRAM, ([\d.]+) MiB read from RAM")
R_FILE = re.compile(r"file tier I/O this request: the OS read ([\d.]+) MB from storage \((\d+) major faults\)")
R_PARK = re.compile(r"conversation cache: parked \d+ tokens in [\d.]+ ms; parked=(\d+) bytes=\d+ evictions=(\d+)")
R_RESTORE = re.compile(r"conversation cache: restored \d+ tokens \((live|checkpoint)\) in")
R_RING = re.compile(r"strata batch \(pipelined, (\d+) groups of (\d+)\): (\d+) group-steps, (\d+) rows in "
                    r"[\d.]+ ms = ([\d.]+) rows/s")
R_MTPKEEP = re.compile(r"\((\d+) of (\d+) MTP proposals kept\)")
R_CHUNK = re.compile(r"layer split: (\d+) prompt tokens in chunks of (\d+) \(not (\d+)\)")

FATAL = ["engine had stopped", "engine reported an error", "no progress for", "stall report",
         "out of memory", "CUDA error", "Traceback"]
WARNRAW = {"remote-read placement FAILED": "NUMA placement FAILED: slice",
           "NUMA at risk": "NUMA placement at risk",
           "low VRAM after load": "of VRAM free with everything loaded - LOW"}


def scope():
    txt = open(ENG, "rb").read().decode("utf-8", "replace").splitlines()
    boots_all = sum(1 for l in txt if "engine started:" in l)
    if SINCE == "boot":
        idx = [i for i, l in enumerate(txt) if "engine started:" in l]
        if not idx:
            raise SystemExit("LANEAUDIT-ERROR no `engine started:` line in %s" % ENG)
        return txt[idx[-1]:], boots_all, "current boot", len(txt)
    if SINCE == "delta":
        try:
            mark = int(open(WM).read().split()[0])
        except Exception:
            raise SystemExit("LANEAUDIT-ERROR no watermark at %s - seed it with the current log "
                             "line count, or audit SINCE=boot first" % WM)
        mark = max(0, min(mark, len(txt)))
        return txt[mark:], boots_all, "since line %d" % mark, len(txt)
    n = int(SINCE)
    return txt[-n:], boots_all, "last %d lines" % n, len(txt)


def p90(xs):
    s = sorted(xs)
    return s[min(len(s) - 1, int(0.90 * (len(s) - 1)))] if s else None


def p10(xs):
    s = sorted(xs)
    return s[int(0.10 * (len(s) - 1))] if s else None


def med(xs):
    return round(statistics.median(xs), 2) if xs else None


def main():
    ln, boots_all, what, total = scope()
    fails, warns, out = [], [], []

    def note(name, value, verdict, detail=""):
        out.append("%-26s %-22s %-6s %s" % (name, value, verdict, detail))
        if verdict == "FAIL":
            fails.append(name)
        elif verdict == "WARN":
            warns.append(name)

    reqs, hits, kvs, resident, ring, chunks = [], [], [], [], [], {}
    file_dirty = 0
    file_mb = 0.0
    file_faults = 0
    blob_reads = 0
    restored = {"live": 0, "checkpoint": 0}
    evict_by_parked, fatal_hits = {}, {k: 0 for k in FATAL}
    raw_warns = {k: 0 for k in WARNRAW}
    reused_yes = 0
    for l in ln:
        for k in FATAL:
            if k in l:
                fatal_hits[k] += 1
        for k, pat in WARNRAW.items():
            if pat in l:
                raw_warns[k] += 1
        m = R_PROMPT.search(l)
        if m:
            g = m.groups()
            reqs.append({"ptok": int(g[0]), "cold": int(g[1]) == 0,
                         "pp": None if g[3] == "NA" else float(g[3]), "gen": int(g[4]),
                         "tg": None if g[5] == "NA" else float(g[5]),
                         "acc": int(g[6]), "off": int(g[7])})
            if int(g[1]) > 0:
                reused_yes += 1
            continue
        m = R_RES.search(l)
        if m:
            resident.append(float(m.group(1)))
            blob_reads += int(m.group(3))
            continue
        m = R_HIT.search(l)
        if m:
            hits.append(float(m.group(1)))
            continue
        m = R_KV.search(l)
        if m:
            kvs.append(float(m.group(1)))
            continue
        m = R_FILE.search(l)
        if m:
            file_mb += float(m.group(1))
            file_faults += int(m.group(2))
            if float(m.group(1)) > 0.0 or int(m.group(2)) > 0:
                file_dirty += 1
            continue
        m = R_PARK.search(l)
        if m:
            ev = int(m.group(2))
            if ev:
                evict_by_parked[m.group(1)] = evict_by_parked.get(m.group(1), 0) + ev
            continue
        m = R_RESTORE.search(l)
        if m:
            restored[m.group(1)] += 1
            continue
        m = R_RING.search(l)
        if m:
            steps, rows = int(m.group(3)), int(m.group(4))
            kept = R_MTPKEEP.search(l)
            ring.append((steps, rows, float(m.group(5)),
                         (int(kept.group(1)), int(kept.group(2))) if kept else None))
            continue
        m = R_CHUNK.search(l)
        if m:
            chunks[m.group(2)] = chunks.get(m.group(2), 0) + 1

    band_of = lambda p: next((i for i, (lo, hi, _) in enumerate(BANDS) if lo <= p < hi), None)
    tg_by, pp_by = {}, {}
    for r in reqs:
        b = band_of(r["ptok"])
        if b is None:
            continue
        if r["tg"] is not None and r["gen"] >= GEN_MIN:
            tg_by.setdefault(b, []).append(r["tg"])
        if r["cold"] and r["pp"] is not None and r["ptok"] > 4000:
            pp_by.setdefault(b, []).append(r["pp"])

    print("scope=%s | requests=%d | boots_in_log=%d | cards=%d%s"
          % (what, len(reqs), boots_all, CARDS,
             "" if SINCE != "delta" else " | watermark=%d->%d" % (int(what.split()[2]), total)))
    boots_here = sum(1 for l in ln if "engine started:" in l)
    if SINCE == "boot":
        note("engine-respawn", "boots_in_boot=1", "PASS", "engine started: lines inside one boot")
    else:
        note("engine-respawn", "restarts=%d" % boots_here,
             "PASS" if boots_here == 0 else ("WARN" if boots_here == 1 else "FAIL"),
             "engine started: lines in the window - 0 is expected, a lane nobody restarted does not "
             "respawn itself")
    nf = sum(fatal_hits.values())
    note("fatal-signatures", str(nf), "FAIL" if nf else "PASS",
         ",".join("%s=%d" % (k, v) for k, v in fatal_hits.items() if v) or "none of the death strings")

    # A delta window can hold a band with three samples, and three samples under live concurrency
    # say nothing about a band rate.  Below the floor the numbers are printed with SKIP: visible, so
    # the window is not silently missing, but never counted as a verdict.
    min_tg, min_pp = (3, 2) if SINCE == "boot" else (6, 4)
    a = ANCHOR[CARDS]["tg"]
    for i, (_, _, lab) in enumerate(BANDS):
        xs = tg_by.get(i) or []
        if not xs:
            continue
        if len(xs) < min_tg:
            note("decode " + lab, "p90 %.1f/%.1f=%.2f" % (p90(xs), a[i], p90(xs) / a[i]), "SKIP",
                 "n=%d below the %d-sample floor for this scope - shown, not judged"
                 % (len(xs), min_tg))
            continue
        r = p90(xs) / a[i]
        v = "PASS" if r >= 0.85 else ("WARN" if r >= 0.70 else "FAIL")
        note("decode " + lab, "p90 %.1f/%.1f=%.2f" % (p90(xs), a[i], r), v,
             "n=%d med=%.1f p10=%.1f" % (len(xs), statistics.median(xs), p10(xs)))
    sh = tg_by.get(0) or []
    dp = (tg_by.get(3) or []) + (tg_by.get(4) or [])
    if (len(sh) or len(dp)) and not (len(sh) >= min_tg and len(dp) >= min_tg):
        note("short-vs-deep inversion", "n %d/%d" % (len(sh), len(dp)), "SKIP",
             "both sides need %d samples in scope" % min_tg)
    if len(sh) >= min_tg and len(dp) >= min_tg:
        ratio = statistics.median(sh) / statistics.median(dp)
        note("short-vs-deep inversion", "med %.1f/%.1f=%.2f" % (statistics.median(sh),
                                                                statistics.median(dp), ratio),
             "FAIL" if ratio < 0.90 else "PASS", "a short window must not be slower than a deep one")
    ap = ANCHOR[CARDS]["pp"]
    for i, (_, _, lab) in enumerate(BANDS):
        xs = pp_by.get(i) or []
        if not xs:
            continue
        if len(xs) < min_pp:
            note("cold pp " + lab, "med %.0f/%.0f=%.2f" % (statistics.median(xs), ap[i],
                                                           statistics.median(xs) / ap[i]), "SKIP",
                 "n=%d below the %d-sample floor for this scope - shown, not judged"
                 % (len(xs), min_pp))
            continue
        r = statistics.median(xs) / ap[i]
        v = "PASS" if r >= 0.75 else ("WARN" if r >= 0.55 else "FAIL")
        note("cold pp " + lab, "med %.0f/%.0f=%.2f" % (statistics.median(xs), ap[i], r), v,
             "n=%d" % len(xs))

    off = sum(r["off"] for r in reqs)
    acc = sum(r["acc"] for r in reqs) / off if off else None
    if acc is not None:
        note("mtp acceptance", "%.3f" % acc, "PASS" if acc >= 0.55 else "WARN",
             "accepted=%d offered=%d" % (sum(r["acc"] for r in reqs), off))
    if ring:
        rpg = [rw / st for st, rw, _, _ in ring if st]
        rps = [x for _, _, x, _ in ring if x]
        keep = [k for _, _, _, k in ring if k]
        note("batch rows/group-step", "%.2f" % statistics.median(rpg),
             "PASS" if statistics.median(rpg) >= 1.3 else "WARN",
             "windows=%d rows/s med=%s%s" % (len(ring), med(rps),
             (" mtp kept %.2f" % (sum(k[0] for k in keep) / sum(k[1] for k in keep))) if keep else ""))
    if hits:
        note("expert hit rate", "p10 %.1f%%" % p10(hits), "PASS" if p10(hits) >= 80 else "WARN",
             "med %.1f%% n=%d" % (statistics.median(hits), len(hits)))
    if kvs:
        note("KV VRAM hit", "p10 %.1f%%" % p10(kvs), "PASS" if p10(kvs) >= 60 else "WARN",
             "med %.1f%% n=%d (low = host-wait pressure)" % (statistics.median(kvs), len(kvs)))
    note("expert blob reads", str(blob_reads), "FAIL" if blob_reads else "PASS",
         "non-zero means the RAM tier missed to storage")
    note("storage reads", "%d req" % file_dirty, "WARN" if file_dirty else "PASS",
         "%.1f MB read from storage, %d major faults over the scope (experts outside the RAM tier; "
         "the page cache should absorb this)" % (file_mb, file_faults))
    if resident:
        vals = sorted(resident)
        note("resident RAM mode", "%.2f GiB" % statistics.median(resident), "PASS",
             "distinct=%d min %.2f max %.2f n=%d" % (len(set(vals)), vals[0], vals[-1], len(vals)))
    note("cache reuse share", "%.2f" % (reused_yes / len(reqs)) if reqs else "n/a", "PASS",
         "requests served with a reused prefix")
    note("cc restore live/ckpt", "%d/%d" % (restored["live"], restored["checkpoint"]), "PASS",
         "live rows replay a parked conversation, checkpoint rows rebuild one")
    if evict_by_parked:
        tot = sum(evict_by_parked.values())
        cap = max(evict_by_parked, key=lambda k: int(k))
        pk, ev = max(evict_by_parked.items(), key=lambda kv: kv[1])
        share = ev / tot
        note("eviction shape", "parked=%s %.0f%%" % (pk, 100 * share),
             "WARN" if (share >= 0.9 and int(pk) >= int(cap)) else "PASS",
             "evictions=%d over %d parked levels, deepest seen parked=%s (everything landing on the "
             "deepest level = residency saturation)" % (tot, len(evict_by_parked), cap))
    if chunks:
        deep = sum(v for k, v in chunks.items() if int(k) < 8192)
        note("prefill chunking", "sizes=%d" % len(chunks), "WARN" if deep else "PASS",
             "logged=%d smallest=%s below8192=%d (chunk geometry varies with live slots: the "
             "cold/cached asymmetry)" % (sum(chunks.values()), min(chunks, key=int), deep))
    for k, v in raw_warns.items():
        if v:
            note(k.replace(" ", "-").lower(), str(v), "WARN", "counted in this scope")

    print("\n".join(out))
    print("\nchecks=%d" % len(out))
    if SINCE == "delta":
        try:
            open(WM, "w").write("%d\n" % total)
            print("watermark-moved %s -> %d" % (WM, total))
        except Exception as exc:
            print("watermark-NOT-moved %s (%s) - rerun the same scope after fixing it" % (WM, exc))
    if fails:
        print("LANEAUDIT-FAIL fails=%d warns=%d :: %s" % (len(fails), len(warns), ",".join(fails)))
    elif warns:
        print("LANEAUDIT-WARN fails=0 warns=%d :: %s" % (len(warns), ",".join(warns)))
    elif len(reqs) < 5 and SINCE != "boot":
        print("LANEAUDIT-THIN fails=0 warns=0 requests=%d - a clean sweep over too little new "
              "traffic to judge rates; audit again after more traffic, or SINCE=boot for the whole "
              "lane" % len(reqs))
    else:
        print("LANEAUDIT-PASS fails=0 warns=0")


main()

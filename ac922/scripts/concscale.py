#!/usr/bin/env python3.11
"""Concurrency scaling: how does aggregate deep decode move from 1 to N conversations?

Why this exists.  The single-stream census over this box's whole request history says a deep stream runs
57-64 tokens/s flat from 32 K to 256 K context, while the operator's real experience is a few tokens per
second with several conversations in flight.  The interference harness's control cell (four distinct
~131 K conversations, no cold read at all) came back at 6.9 tokens/s per stream, 27.8 aggregate.  So the
question is the scaling curve itself, not a knob.

Two bugs in the first version of this tool are worth recording, because both would have produced a
confident wrong answer:

  * EOS fakes concurrency.  Synthetic deep prompts stop on their own: three of four "900 token" streams
    ended under 50 tokens, so a cell nominally at n=4 was really measuring one stream plus three stubs.
    A cell is now a fixed WALL-clock budget with each stream re-issued until the budget ends, and both a
    busy-only and a wall-inclusive rate are reported so restore overhead is visible rather than hidden.
  * /proc/stat fields were mislabeled: field 3 is idle, not system, so `sys_pct` read ~95 % on an idle
    box.  usr / sys / iowait / idle are now taken from the right columns and idle is reported too, which
    is the sanity check that catches exactly this class of error.

Attribution is by construction, not by timestamp: the engine prints a request's summary when it finishes
and its decode-timing line carries no wall clock (the watcher stamps rows with its own drain time), so
filtering rows by a span silently selects the wrong ones.  Each cell takes the rows and request lines
that appeared between its own two drains.

Numbers, medians and hashes only - never prompt, completion or template text.  The follow-up instruction
text is generated here and is only ever sent, never printed.

Usage (detached, per runbook 328 - never from an interactive ssh session):
  setsid nohup bash ac922/scripts/concscale.sh > /data/work/concscale.log 2>&1 < /dev/null &
"""
import argparse, json, os, re, statistics as st, subprocess, threading, time, urllib.request, zlib

WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november "
         "oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu").split()
KEEP = "Enumerate the integers from 1 upward, one per line. Do not stop, do not summarize. Begin now."
ROWS = os.environ.get("ROWS", "/data/work/decodetiming/rows-%s.jsonl" % time.strftime("%Y%m"))
WATCH = os.environ.get("WATCH", "/data/work/ac922-strata-nvfp4/ac922/scripts/decodetiming_watch.py")
ENGINE = os.environ.get("ENGINE_LOG", "/data/champion/strata-nvfp4-engine.log")
OUT = os.environ.get("OUT", "/data/work/concscale-cells.jsonl")
DISK = os.environ.get("DISK", "nvme0n1")

GEN = re.compile(r"prompt (?P<ptok>\d+) tokens = (?P<reuse>\d+) reused \+ (?P<rd>\d+) read in "
                 r"(?P<rdms>\d+) ms \((?P<pp>[\d.]+) tok/s\), (?P<gen>\d+) generated in "
                 r"(?P<gms>\d+) ms \((?P<tg>[\d.]+) tok/s\), drafts accepted (?P<acc>\d+) of "
                 r"(?P<drf>\d+)")
KEY = os.environ.get("STRATA_API_KEY", "")
OFF = {"start": 0}
REPO = os.environ.get("REPO", "/data/work/ac922-strata-nvfp4")
# A natural-text corpus for the measurement-validity check: random word lists are the worst case for
# query-sparse attention (nothing to attend selectively to), so a deep window measured on them can be
# slow for a reason that has nothing to do with the box.  The prose source is this repo's own
# documentation - plain English text, no model output, read off disk.
PROSE_SRC = [os.path.join(REPO, f) for f in
             ("ac922/DEVLOG.md", "ac922/DECISIONS.md", "docs/NVFP4.md", "README.md")]


def prose_pool():
    chunks = []
    for f in PROSE_SRC:
        try:
            txt = open(f, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for para in re.split(r"\n\s*\n", txt):
            para = " ".join(para.split())
            if 40 < len(para) < 1200:
                chunks.append(para)
    return chunks


def prose_text(n_chars, salt, pool):
    v = zlib.crc32(salt.encode()) & 0x7FFFFFFF
    out, total = [], 0
    while total < n_chars:
        v = (v * 1103515245 + 12345) & 0x7FFFFFFF
        c = pool[v % len(pool)]
        out.append(c)
        total += len(c) + 1
    return (" ".join(out))[:n_chars]


def corpus_text(corpus, n_chars, salt, pool):
    """Both arms are sized in CHARACTERS so a target token count maps to each the same way."""
    if corpus == "prose":
        return prose_text(n_chars, salt, pool)
    # Generate over a word count derived from the target, then cut to exactly n_chars: the calibration
    # probe measured this generator in characters per token, so sizing by words-then-join overshoots
    # (a 131 K target came back as 157 K) and the arms would not be at equal depth.
    return (" ".join(uniq_text(max(1, int(n_chars / 6.0)), salt)))[:n_chars]


def probe_tokens_per_char(url, text):
    """One cheap request, then read the engine's own token count for it back off the log.

    The probe asks for no checkpoint: a calibration sample must not park a chain in the conversation
    cache, because parked deep chains are the very residency pressure the curve is meant to measure."""
    off = os.path.getsize(ENGINE)
    plain(url, [{"role": "user", "content": text}], {"temperature": 0.0,
                                                     "strata_checkpoint": False})
    time.sleep(0.7)
    try:
        with open(ENGINE, "rb") as f:
            f.seek(off)
            blob = f.read().decode("utf-8", "replace")
    except Exception:
        return None
    hits = [int(m.group("ptok")) for m in GEN.finditer(blob)]
    return (hits[-1] / float(len(text))) if hits else None


def uniq_text(n, salt):
    v = zlib.crc32(salt.encode()) & 0x7FFFFFFF
    out = []
    for i in range(n):
        v = (v * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(WORDS[(v + i * 7) % len(WORDS)])
        if i % 4 == 3:
            out.append(str((v % 999983) + 1000))
    return " ".join(out)


def call(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + KEY})
    return urllib.request.urlopen(req, timeout=3600)


def plain(url, messages, sampling=None):
    body = {"model": "strata", "messages": messages, "max_tokens": 1, "temperature": 0,
            "stream": False}
    if sampling:
        body["sampling"] = sampling
    t0 = time.time()
    with call(url, body) as f:
        json.loads(f.read())
    return time.time() - t0


def sustained(url, messages, budget, token_cap, box):
    """Re-issue the same continuation until `budget` wall seconds have passed.

    The message list never grows, so every iteration is the same parked chain coming back and decoding
    the same continuation: the work is decode, and the only thing that varies with n is how the box shares
    it.  `box` accumulates stamps, per-iteration busy spans and the inter-iteration gaps (restore+queue).
    """
    box["t0"] = time.time()
    while time.time() - box["t0"] < budget:
        stamps = []
        t_it = time.time()
        try:
            body = {"model": "strata", "messages": messages, "max_tokens": token_cap,
                    "temperature": 0, "stream": True}
            with call(url, body) as f:
                for raw in f:
                    if raw.startswith(b"data:") and b"[DONE]" not in raw:
                        stamps.append(time.time())
        except Exception as exc:
            box["errors"] += 1
            box["last_err"] = type(exc).__name__
        t_end = time.time()
        if len(stamps) >= 2:
            box["tokens"] += len(stamps)
            box["busy"] += stamps[-1] - stamps[0]
            box["spans"].append((stamps[0], stamps[-1], len(stamps)))
        box["iters"] += 1
    box["t1"] = time.time()


def cpu_sample():
    with open("/proc/stat") as f:
        v = [int(x) for x in f.readline().split()[1:9]]
    # user nice system idle iowait irq softirq steal
    return {"usr": v[0] + v[1], "sys": v[2] + v[5] + v[6], "iowait": v[4], "idle": v[3],
            "total": sum(v)}


def disk_sample():
    with open("/proc/diskstats") as f:
        for ln in f:
            p = ln.split()
            if p[2] == DISK:
                return {"rs": int(p[3]), "rsec": int(p[5]), "wsec": int(p[9])}
    return {"rs": 0, "rsec": 0, "wsec": 0}


class Probe(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.cur = None

    def open(self, name):
        with self.lock:
            self.res = getattr(self, "res", {})
            self.res[name] = {"name": name, "t0": time.time(), "c_first": cpu_sample(),
                              "d_first": disk_sample(), "c_last": None, "d_last": None,
                              "t_last": time.time(), "peak_rd": 0.0}
            self.cur = self.res[name]

    def close(self, name):
        with self.lock:
            if self.cur and self.cur["name"] == name:
                self.cur = None

    def run(self):
        while True:
            with self.lock:
                cur = self.cur
            if cur is not None:
                c, d, now = cpu_sample(), disk_sample(), time.time()
                secs = max(1.0, now - cur["t0"])
                cur["peak_rd"] = max(cur["peak_rd"],
                                     (d["rsec"] - cur["d_first"]["rsec"]) * 512.0 / 2 ** 20 / secs)
                cur["peak_wr"] = max(cur.get("peak_wr", 0.0),
                                     (d["wsec"] - cur["d_first"]["wsec"]) * 512.0 / 2 ** 20 / secs)
                cur["c_last"], cur["d_last"], cur["t_last"] = c, d, now
            time.sleep(0.5)

    def report(self, name):
        cur = getattr(self, "res", {}).get(name)
        if not cur:
            return {}
        if cur["c_last"] is None:
            return {}
        # Machine-wide shares: every /proc/stat field is summed over all 176 CPUs, so the honest
        # denominator is the sum of the deltas, not a core count.  idle is reported because a cell that
        # shows no idle while tg is low is a contention story, and one that shows idle is not.
        span = max(1.0, cur["t_last"] - cur["t0"])
        dc = {k: max(0, cur["c_last"][k] - cur["c_first"][k]) for k in
              ("usr", "sys", "iowait", "idle", "total")}
        tot = max(1, dc["total"])
        dd = {k: max(0, cur["d_last"][k] - cur["d_first"][k]) for k in ("rs", "rsec", "wsec")}
        return {"span_s": round(span, 1),
                "usr_pct": round(100.0 * dc["usr"] / tot, 1),
                "sys_pct": round(100.0 * dc["sys"] / tot, 1),
                "iowait_pct": round(100.0 * dc["iowait"] / tot, 1),
                "idle_pct": round(100.0 * dc["idle"] / tot, 1),
                "rd_MBps": round(cur["peak_rd"], 1),
                "wr_MBps": round(cur.get("peak_wr", 0.0), 1),
                "rd_iops": round(dd["rs"] / span, 1)}


def drain():
    try:
        subprocess.run(["python3.11", WATCH], timeout=120, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, env=dict(os.environ, DT_QUIET="1"))
    except Exception:
        pass


def row_count():
    try:
        with open(ROWS) as f:
            return sum(1 for _ in f)
    except FileNotFoundError:
        return 0


def tail_rows(n):
    if n <= 0:
        return []
    try:
        with open(ROWS, errors="ignore") as f:
            return [json.loads(l) for l in f.readlines()[-n:]]
    except Exception:
        return []


def tail_req_lines(n):
    if n <= 0:
        return []
    try:
        with open(ENGINE, "rb") as f:
            f.seek(OFF["start"])
            blob = f.read().decode("utf-8", "replace")
    except Exception:
        return []
    found = [m.groupdict() for m in GEN.finditer(blob)]
    return [d for d in found if int(d["gen"]) >= 20][-n:]


def med(vals):
    vals = [v for v in vals if v is not None]
    return st.median(vals) if vals else float("nan")


# The engine's own census of the pipelined batch ring, flushed when every slot goes idle:
#   strata batch (pipelined, G groups of S): W group-steps, R rows in T ms = X rows/s
#       (admissions included)[; adaptive tier ...] (A of B MTP proposals kept)
# rows and group-steps reset at each flush, so summing the lines inside one cell's byte range attributes
# the ring's work to that cell.  This is the authoritative description of a concurrent window.  The
# decode-timing line's tokens/window is emitted on the solo path and reads 1.00 while the ring runs, which
# is why the first version of this harness mistook a metric-population mismatch for a lost speculative
# multiplier (H.35 correction, 2026-11-23).
CENSUS = re.compile(
    r"batch \(pipelined, (\d+) groups of (\d+)\): (\d+) group-steps, (\d+) rows in ([\d.]+) ms"
    r"(?:[^\n]*?\((\d+) of (\d+) MTP proposals kept\))?")
assert CENSUS.search(
    "strata batch (pipelined, 4 groups of 1): 3193 group-steps, 5390 rows in 71271 ms = 75.6 rows/s "
    "(admissions included); adaptive tier (STRATA_PIPE_ADAPT_EVERY): 321 rounds, 1209 experts swapped in, "
    "2906 ms (2197 of 3186 MTP proposals kept)").groups() == ("4", "1", "3193", "5390", "71271",
                                                              "2197", "3186")


def tail_census():
    """Aggregate every ring census line emitted inside this cell's byte range."""
    try:
        with open(ENGINE, "rb") as f:
            f.seek(OFF["start"])
            blob = f.read().decode("utf-8", "replace")
    except Exception:
        return {}
    steps = rows = kept = total = 0.0
    ms = 0.0
    shape = ""
    lines = 0
    for m in CENSUS.finditer(blob):
        g, s, st_, rw, t = (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            int(m.group(4)), float(m.group(5)))
        steps += st_
        rows += rw
        ms += t
        lines += 1
        if m.group(6) is not None:
            kept += int(m.group(6))
            total += int(m.group(7))
        shape = "%d/%d" % (g, s)
    if not lines or ms <= 0 or steps <= 0:
        return {"cen_lines": lines, "cen_shape": shape}
    groups = int(shape.split("/")[0]) if "/" in shape else 1
    return {"cen_lines": lines, "cen_shape": shape, "cen_steps": int(steps), "cen_rows": int(rows),
            "cen_ms": int(ms), "cen_step_ms": round(ms / steps, 1),
            "cen_rows_step": round(rows / steps, 2), "cen_rows_s": round(1000.0 * rows / ms, 1),
            "cen_step_rate": round(1000.0 * steps / ms, 1),
            "cen_slot_rate": round(1000.0 * steps / ms / max(1, groups), 1),
            "cen_mtp_keep": round(kept / total, 3) if total else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=4)
    ap.add_argument("--deep-words", type=int, default=42000)
    ap.add_argument("--budget", type=float, default=80.0)
    ap.add_argument("--token-cap", type=int, default=1200)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--port", default=os.environ.get("PORT", "8091"))
    ap.add_argument("--label", default=os.environ.get("LABEL", "shape"))
    ap.add_argument("--corpus", default="words",
                    help="comma list of words,prose - prose is the measurement-validity arm")
    ap.add_argument("--target-tokens", type=int, default=0,
                    help="size each session to this many prompt tokens via a calibration probe")
    a = ap.parse_args()
    if not KEY:
        raise SystemExit("STRATA_API_KEY missing - source /etc/profile.d/env.sh")
    url = "http://127.0.0.1:%s/v1/chat/completions" % a.port
    probe = Probe()
    probe.start()

    shape = subprocess.run(["grep", "-a", "-o", "-E", "-e",
                            r"batch-groups [a-z0-9]+: [0-9]+ groups of [0-9]+ slot[s]?[^\n]*",
                            ENGINE], capture_output=True, text=True).stdout.strip().splitlines()
    print("label=%s boot shape: %s" % (a.label, sorted(set(shape))[-1][:120] if shape else "none"),
          flush=True)

    pool = prose_pool()
    ratios = {}
    all_cells = []
    for corpus in [c.strip() for c in a.corpus.split(",") if c.strip()]:
        if corpus == "prose" and not pool:
            print("  prose: no readable documentation corpus on this host, arm skipped", flush=True)
            continue
        sessions = []
        for i in range(a.sessions):
            salt = "cs3-%d-%d-%s" % (os.getpid(), i, corpus)
            chars = int(a.deep_words * 6.0)
            if a.target_tokens and corpus not in ratios:
                ratios[corpus] = probe_tokens_per_char(
                    url, corpus_text(corpus, 20000, "calib-%s" % corpus, pool))
            if a.target_tokens:
                r = ratios.get(corpus)
                if r:
                    chars = int(a.target_tokens / r)
                    print("  %s calibrated %.5f tokens/char -> %d chars for %d tokens" % (
                        corpus, r, chars, a.target_tokens), flush=True)
                else:
                    print("  %s: probe returned no token count, using %d chars" % (corpus, chars),
                          flush=True)
            text = corpus_text(corpus, chars, salt, pool)
            w = plain(url, [{"role": "user", "content": text}])
            sessions.append([{"role": "user", "content": text},
                             {"role": "assistant", "content": "ok"},
                             {"role": "user", "content": KEEP}])
            print("  %s session %d built wall=%.1f s chars=%d" % (corpus, i, w, len(text)), flush=True)

        cells = []
        ns = list(range(1, a.sessions + 1))
        for rnd in range(1, a.rounds + 1):
            for n in (ns if rnd % 2 else ns[::-1]):
                boxes = [{"tokens": 0, "busy": 0.0, "spans": [], "iters": 0, "errors": 0,
                          "last_err": ""} for _ in range(n)]
                OFF["start"] = os.path.getsize(ENGINE)
                drain()
                n0 = row_count()
                probe.open("c%s_r%d_n%d" % (corpus, rnd, n))
                th = [threading.Thread(target=sustained,
                                       args=(url, sessions[i], a.budget, a.token_cap, boxes[i]))
                      for i in range(n)]
                for t in th:
                    t.start()
                    time.sleep(0.05)
                for t in th:
                    t.join()
                probe.close("c%s_r%d_n%d" % (corpus, rnd, n))
                drain()
                rr = tail_rows(row_count() - n0)
                rq = tail_req_lines(n * 6)

                busy_rate, wall_rate = [], []
                for b in boxes:
                    life = max(1.0, b.get("t1", time.time()) - b.get("t0", time.time()))
                    if b["tokens"] > 20:
                        if b["busy"] > 1:
                            busy_rate.append(b["tokens"] / b["busy"])
                        wall_rate.append(b["tokens"] / life)
                cell = {"corpus": corpus, "round": rnd, "n": n, "label": a.label,
                        "iters": sum(b["iters"] for b in boxes),
                        "errors": sum(b["errors"] for b in boxes),
                        "last_err": next((b["last_err"] for b in boxes if b["last_err"]), ""),
                        "tokens_total": sum(b["tokens"] for b in boxes),
                        "tg_busy": round(med(busy_rate), 2), "agg_busy": round(sum(busy_rate), 2),
                        "tg_wall": round(med(wall_rate), 2), "agg_wall": round(sum(wall_rate), 2),
                        "duty": round(sum(b["busy"] for b in boxes) /
                                      max(1.0, sum(max(1.0, b.get("t1", 1) - b.get("t0", 0))
                                                   for b in boxes)), 3),
                        "n_req_lines": len(rq), "n_rows": len(rr),
                        "mswin": round(med([r.get("mswin") for r in rr]), 2),
                        "tpw": round(med([r.get("tpw") for r in rr]), 2),
                        "verify": round(med([r.get("verify") for r in rr]), 2),
                        "gpu_reach": round(med([r.get("gpu_reach") for r in rr]), 2),
                        "cpu": round(med([r.get("cpu") for r in rr]), 2),
                        "host": round(med([r.get("host") for r in rr]), 2),
                        "restore_ms": round(med([float(d["rdms"]) for d in rq]), 1),
                        "engine_tg": round(med([float(d["tg"]) for d in rq]), 2),
                        "acc": round(med([int(d["acc"]) / max(1, int(d["drf"])) for d in rq]), 3),
                        "ptok": int(float(rq[-1]["ptok"])) if rq else 0}
                cell.update(probe.report("c%s_r%d_n%d" % (corpus, rnd, n)))
                cell.update(tail_census())
                cells.append(cell)
                all_cells.append(cell)
                print(json.dumps(cell, sort_keys=True), flush=True)
                with open(OUT, "a") as f:
                    f.write(json.dumps(cell) + "\n")

        print("\ncurve for corpus=%s: %-3s %9s %9s %9s %9s %8s %8s %8s %8s %8s %7s %6s" % (
            corpus, "n", "agg_wall", "agg_busy", "tg_wall", "tg_busy", "mswin", "tpw", "reach",
            "cpu", "restore", "duty", "err"), flush=True)
        for n in ns:
            sel = [c for c in cells if c["n"] == n]
            if not sel:
                continue
            print("%-4d %8.1f %9.1f %9.2f %9.2f %8.1f %8.2f %8.2f %8.2f %8.0f %7.2f %6d" % (
                n, med([c["agg_wall"] for c in sel]), med([c["agg_busy"] for c in sel]),
                med([c["tg_wall"] for c in sel]), med([c["tg_busy"] for c in sel]),
                med([c["mswin"] for c in sel]), med([c["tpw"] for c in sel]),
                med([c["gpu_reach"] for c in sel]), med([c["cpu"] for c in sel]),
                med([c["restore_ms"] for c in sel]), med([c["duty"] for c in sel]),
                sum(c["errors"] for c in sel)), flush=True)
            ring = [c for c in sel if c.get("cen_rows_step")]
            if ring:
                print("     ring %s  shape=%s step=%s ms  rows/step=%s  rows/s=%s  "
                      "mtp_keep=%s  per-slot=%s tok/s  (engine census, %s lines)" % (
                    "n=%-2d" % n, med([c["cen_shape"] for c in ring]),
                    med([c["cen_step_ms"] for c in ring]), med([c["cen_rows_step"] for c in ring]),
                    med([c["cen_rows_s"] for c in ring]), med([c["cen_mtp_keep"] for c in ring]),
                    med([c["agg_wall"] for c in ring]) / n,
                    int(sum(c["cen_lines"] for c in ring))), flush=True)
        base = med([c["agg_wall"] for c in cells if c["n"] == 1])
        print("agg_wall(n)/agg_wall(1): " + " ".join(
            "n=%d %.2fx" % (n, med([c["agg_wall"] for c in cells if c["n"] == n]) / base)
            if base and base == base else "na" for n in ns), flush=True)

    if len(all_cells) and len(set(c["corpus"] for c in all_cells)) > 1:
        print("\ncorpus validity at equal n (prose vs words, same depth):", flush=True)
        for n in sorted(set(c["n"] for c in all_cells)):
            g = {}
            for corpus in sorted(set(c["corpus"] for c in all_cells)):
                sel = [c for c in all_cells if c["n"] == n and c["corpus"] == corpus]
                if sel:
                    g[corpus] = (med([c["tg_wall"] for c in sel]), med([c["mswin"] for c in sel]),
                                 med([c["agg_wall"] for c in sel]))
            if len(g) == 2:
                (wt, wm, wa), (pt, pm, pa) = g["words"], g["prose"]
                print("  n=%d words tg %.2f mswin %.1f agg %.1f | prose tg %.2f mswin %.1f "
                      "agg %.1f | prose/words tg x%.2f" % (n, wt, wm, wa, pt, pm, pa,
                                                            (pt / wt) if wt else float("nan")),
                      flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()

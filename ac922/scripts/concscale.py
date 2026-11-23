#!/usr/bin/env python3.11
"""Concurrency scaling: how does aggregate decode move from 1 to N distinct deep conversations?

The interference harness's control cell (round 1 CTL, no cold read at all, four distinct ~131 K
conversations decoding together) came back at 6.89 tokens/s per stream, 27.8 aggregate, while the
whole-boot request census for this box says a single deep stream runs 57-64 tokens/s flat from 32 K to
256 K (DEVLOG H.34's rows plus the 20 881-line request parse).  So four separate deep conversations
scale *negatively*: 0.46x of one stream's aggregate.  That is the operator's complaint, and it is a
different complaint from the one the arms were built to test.

This harness measures the curve instead of guessing its shape:

  agg(n) and per-stream tg for n = 1..N, distinct deep conversations, paired rounds with the n order
  rotated, so a slow drift across the boot cannot masquerade as a point on the curve.

Attribution is by construction rather than by timestamp: the engine prints each request's summary only
when that request finishes and its decode-timing line carries no wall clock (the watcher stamps rows
with its own drain time), so a span filter silently selects the wrong rows - the first harness proved
that by returning 5 rows for an 80 s span.  Here each cell takes exactly the `n` request lines and the
`n` decode-timing rows that appeared between its own two drains, which is the set of summaries its own
streams wrote.

/proc/stat and /proc/diskstats are sampled across each cell so a point on the curve also carries the
system %, iowait % and nvme read MB/s it was achieved under.

Numbers, medians and hashes only - never prompt, completion or template text.

Usage (detached, per runbook 328 - never from an interactive ssh session):
  setsid nohup bash ac922/scripts/concscale.sh > /data/work/concscale.log 2>&1 < /dev/null &
"""
import argparse, json, os, re, statistics as st, subprocess, threading, time, urllib.request, zlib

WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november "
         "oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu").split()
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


def plain(url, messages):
    body = {"model": "strata", "messages": messages, "max_tokens": 1, "temperature": 0,
            "stream": False}
    t0 = time.time()
    with call(url, body) as f:
        json.loads(f.read())
    return time.time() - t0


def run_stream(url, messages, max_tokens, box):
    body = {"model": "strata", "messages": messages, "max_tokens": max_tokens, "temperature": 0,
            "stream": True}
    box["send"] = time.time()
    with call(url, body) as f:
        for raw in f:
            if raw.startswith(b"data:") and b"[DONE]" not in raw:
                box["stamps"].append(time.time())
    box["end"] = time.time()


def cpu_sample():
    with open("/proc/stat") as f:
        v = [int(x) for x in f.readline().split()[1:9]]
    return (v[0] + v[1], v[2] + v[3] + v[4] + v[5], v[6], sum(v))


def disk_sample():
    with open("/proc/diskstats") as f:
        for ln in f:
            p = ln.split()
            if p[2] == DISK:
                return (int(p[3]), int(p[5]), int(p[9]))
    return (0, 0, 0)


class Probe(threading.Thread):
    """Accumulates /proc deltas into the named span; a cell opens one and closes it at its end."""

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.cur = None
        self.done = {}

    def open(self, name):
        with self.lock:
            self.cur = {"name": name, "t0": time.time(), "c": cpu_sample(), "d": disk_sample(),
                        "peak_rd": 0.0}
            self.done[name] = self.cur

    def close(self, name):
        with self.lock:
            if self.cur and self.cur["name"] == name:
                self.cur = None

    def run(self):
        while True:
            with self.lock:
                cur = self.cur
            if cur is not None:
                c, d = cpu_sample(), disk_sample()
                secs = max(0.5, time.time() - cur["t0"])
                cur["last_c"], cur["last_d"] = c, d
                cur["mbps"] = (d[1] - cur["d"][1]) * 512.0 / 2 ** 20 / secs
            time.sleep(0.5)

    def report(self, name):
        cur = self.done.get(name)
        if not cur or "last_c" not in cur:
            return {}
        c0, c1 = cur["c"], cur["last_c"]
        tot = max(1, c1[3] - c0[3])
        return {"span_s": round(max(1.0, cur["last_d"] and time.time() - cur["t0"] or 1.0), 1),
                "usr_pct": round(100.0 * (c1[0] - c0[0]) / tot, 1),
                "sys_pct": round(100.0 * (c1[1] - c0[1]) / tot, 1),
                "iowait_pct": round(100.0 * (c1[2] - c0[2]) / tot, 1),
                "rd_MBps": round(cur.get("mbps", 0.0), 1)}


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
    """The n decode-timing rows the last drain appended - this cell's own stream summaries."""
    if n <= 0:
        return []
    try:
        with open(ROWS, errors="ignore") as f:
            return [json.loads(l) for l in f.readlines()[-n:]]
    except Exception:
        return []


def tail_req_lines(n):
    """The n request summaries written since the cell's log offset, newest last."""
    if n <= 0:
        return []
    try:
        with open(ENGINE, "rb") as f:
            f.seek(OFF["start"])
            blob = f.read().decode("utf-8", "replace")
    except Exception:
        return []
    found = [m.groupdict() for m in GEN.finditer(blob)]
    keep = [d for d in found if int(d["gen"]) >= 50]
    return keep[-n:]


def med(vals):
    vals = [v for v in vals if v is not None]
    return st.median(vals) if vals else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=4)
    ap.add_argument("--deep-words", type=int, default=42000)
    ap.add_argument("--tokens", type=int, default=900)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--port", default=os.environ.get("PORT", "8091"))
    ap.add_argument("--label", default=os.environ.get("LABEL", "shape"))
    a = ap.parse_args()
    if not KEY:
        raise SystemExit("STRATA_API_KEY missing - source /etc/profile.d/env.sh")
    url = "http://127.0.0.1:%s/v1/chat/completions" % a.port
    probe = Probe()
    probe.start()

    shape = subprocess.run(["grep", "-a", "-o", "-E", "-e",
                            r"batch-groups [a-z0-9]+: [0-9]+ groups of [0-9]+ slot[s]?[^\n]*",
                            ENGINE], capture_output=True, text=True)
    print("label=%s boot shape lines=%d" % (a.label, len(shape.stdout.strip().splitlines())), flush=True)
    for ln in sorted(set(shape.stdout.strip().splitlines()))[-3:]:
        print("  shape: %s" % ln[:110], flush=True)

    sessions = []
    for i in range(a.sessions):
        text = uniq_text(a.deep_words, "cs-%d-%d" % (os.getpid(), i))
        w = plain(url, [{"role": "user", "content": text}])
        sessions.append([{"role": "user", "content": text}, {"role": "assistant", "content": "ok"}])
        print("  session %d built wall=%.1f s" % (i, w), flush=True)

    cells = []
    ns = list(range(1, a.sessions + 1))
    for rnd in range(1, a.rounds + 1):
        for n in (ns if rnd % 2 else ns[::-1]):
            boxes = [{"stamps": []} for _ in range(n)]
            OFF["start"] = os.path.getsize(ENGINE)
            n0 = row_count()
            drain()
            n0 = row_count()
            probe.open("r%d_n%d" % (rnd, n))
            th = [threading.Thread(target=run_stream,
                                   args=(url, sessions[i] + [{"role": "user", "content": "continue"}],
                                         a.tokens, boxes[i])) for i in range(n)]
            for t in th:
                t.start()
                time.sleep(0.05)
            for t in th:
                t.join()
            probe.close("r%d_n%d" % (rnd, n))
            drain()
            rr = tail_rows(row_count() - n0)
            rq = tail_req_lines(n)

            per = []
            for b in boxes:
                if len(b["stamps"]) >= 2:
                    span = b["stamps"][-1] - b["stamps"][0]
                    if span > 1:
                        per.append((len(b["stamps"]) - 1) / span)
            eng_tg = [float(d["tg"]) for d in rq]
            cell = {"round": rnd, "n": n, "label": a.label,
                    "streams": len(boxes), "per_stream_stamps": round(med(per), 2),
                    "agg_stamps": round(sum(per), 2),
                    "per_stream_engine": round(med(eng_tg), 2),
                    "agg_engine": round(sum(float(x) for x in eng_tg), 2) if eng_tg else 0.0,
                    "n_req_lines": len(rq), "n_rows": len(rr),
                    "mswin": round(med([r.get("mswin") for r in rr]), 2),
                    "tpw": round(med([r.get("tpw") for r in rr]), 2),
                    "verify": round(med([r.get("verify") for r in rr]), 2),
                    "gpu_reach": round(med([r.get("gpu_reach") for r in rr]), 2),
                    "cpu": round(med([r.get("cpu") for r in rr]), 2),
                    "host": round(med([r.get("host") for r in rr]), 2),
                    "restore_ms": round(med([float(d["rdms"]) for d in rq]), 1),
                    "pp_toks": round(med([float(d["pp"]) for d in rq]), 1),
                    "acc": round(med([int(d["acc"]) / max(1, int(d["drf"])) for d in rq]), 3),
                    "cell_wall": round(max(b["end"] - b["send"] for b in boxes), 1)}
            cell.update(probe.report("r%d_n%d" % (rnd, n)))
            cells.append(cell)
            print(json.dumps(cell, sort_keys=True), flush=True)
            with open(OUT, "a") as f:
                f.write(json.dumps(cell) + "\n")

    print("\nscaling curve, label=%s (per-round then median):" % a.label, flush=True)
    print("%-3s %8s %9s %9s %9s %8s %8s %8s %8s %9s" % (
        "n", "agg_eng", "per_eng", "agg_stp", "mswin", "tpw", "reach", "cpu", "restore",
        "sys%"), flush=True)
    for n in ns:
        sel = [c for c in cells if c["n"] == n]
        if not sel:
            continue
        print("%-3d %8.1f %9.2f %9.2f %9.1f %8.2f %8.2f %8.2f %8.0f %9.1f" % (
            n, med([c["agg_engine"] for c in sel]), med([c["per_stream_engine"] for c in sel]),
            med([c["agg_stamps"] for c in sel]), med([c["mswin"] for c in sel]),
            med([c["tpw"] for c in sel]), med([c["gpu_reach"] for c in sel]),
            med([c["cpu"] for c in sel]), med([c["restore_ms"] for c in sel]),
            med([c.get("sys_pct", float("nan")) for c in sel])), flush=True)
    base = med([c["agg_engine"] for c in cells if c["n"] == 1]) or float("nan")
    print("\nagg(n)/agg(1): " + " ".join("n=%d %.2fx" % (
        n, (med([c["agg_engine"] for c in cells if c["n"] == n]) / base) if base == base else float(
            "nan")) for n in ns), flush=True)
    print("DONE", flush=True)


OFF = {"start": 0}
if __name__ == "__main__":
    main()

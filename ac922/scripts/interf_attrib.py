#!/usr/bin/env python3.11
"""Interference attribution: does a deep cold prefill hurt decoding streams through storage or CPU?

DEVLOG H.34 found the pathological decode windows at 87-92 % `cpu` (315-567 ms/window) while a deep
cold prefill ran beside them, with GPU-reach only 2 %.  Two causes were filed in the same entry and
they need different fixes: (a) the pack, the 512 GiB conversation-disk cold tier and the OS page cache
all sit on one nvme0n1, so a cold read is a storage event; (b) the engine's host-side expert work is a
CPU-scheduling event.  The request-level `aux_cpus` knob cannot arbitrate this - H.16/H.17 closed it as
neutral and it owes no boot.

So this harness discriminates instead of sweeping.  Per round it builds ONE unique deep prompt and
interferes with it twice: I1 is the first pass (cold, faults expert pages in from storage), I2 repeats
the same bytes with `strata_checkpoint: false` so it never parks in the conversation cache - by
construction I2 redoes I1's expert reads against the page cache I1 just filled, so it performs the same
prefill compute with ideally near-zero major faults.  Both are paired against CTL, an equally long span
in which the streams decode undisturbed:

  I2 ~= CTL, I1 >> CTL   -> storage-bound: split the pack off the conversation disk / throttle the tier
  I2 ~= I1 >> CTL        -> host-CPU-bound: the --pool-cores / STRATA_POOL_SPIN_US family
  I2 in between          -> both; price each by how much of I1's damage survives into I2

Two honest limits.  Residency warms monotonically inside a round, so a later arm can inherit a fitter
engine than an earlier one: the parity rotation makes that show up as disagreement between orderings
rather than as an arm effect, and round 1 is reported separately.  And the engine's per-request line
appears when the request finishes, so the log slice is taken at the interference's own completion and
before the decode streams report, otherwise their lines would be read as this request's.

Numbers, medians and hashes only - never prompt, completion or template text.

Usage (detached, per runbook 328 - never from an interactive ssh session):
  setsid nohup bash ac922/scripts/interf_attrib.sh > /data/work/interfab.log 2>&1 < /dev/null &
"""
import argparse, json, math, os, re, statistics as st, subprocess, threading, time, urllib.request, zlib

WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november "
         "oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu").split()
ROWS = os.environ.get("ROWS", "/data/work/decodetiming/rows-%s.jsonl" % time.strftime("%Y%m"))
WATCH = os.environ.get("WATCH", "/data/work/ac922-strata-nvfp4/ac922/scripts/decodetiming_watch.py")
ENGINE = os.environ.get("ENGINE_LOG", "/data/champion/strata-nvfp4-engine.log")
CELLS = os.environ.get("CELLS", "/data/work/interfab-cells.jsonl")
DISK = os.environ.get("DISK", "nvme0n1")

# The engine prints one line per served request; only its numeric fields are ever kept.
REQ_RE = re.compile(r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms "
                    r"\(([\d.]+) tok/s\), (\d+) generated in (\d+) ms")
IO_RE = re.compile(r"OS read ([0-9,.]+) MB from storage \(([0-9,]+) major faults\) for "
                   r"([0-9,.]+) MB of expert reads; page cache held ([0-9,.]+) MB")

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


def plain(url, messages, sampling=None):
    body = {"model": "strata", "messages": messages, "max_tokens": 1,
            "temperature": 0, "stream": False}
    if sampling:
        body["sampling"] = sampling
    t0 = time.time()
    with call(url, body) as f:
        json.loads(f.read())
    return t0, time.time() - t0


def streamed(url, messages, max_tokens, stamps):
    body = {"model": "strata", "messages": messages, "max_tokens": max_tokens,
            "temperature": 0, "stream": True}
    with call(url, body) as f:
        for raw in f:
            if raw.startswith(b"data:") and b"[DONE]" not in raw:
                stamps.append(time.time())
    return stamps


def cpu_sample():
    with open("/proc/stat") as f:
        v = [int(x) for x in f.readline().split()[1:9]]
    return {"user": v[0] + v[1], "sys": v[2] + v[3] + v[4] + v[5], "iowait": v[6], "total": sum(v)}


def disk_sample():
    with open("/proc/diskstats") as f:
        for ln in f:
            p = ln.split()
            if p[2] == DISK:
                return {"rs": int(p[3]), "rsec": int(p[5]), "wsec": int(p[9])}
    return {"rs": 0, "rsec": 0, "wsec": 0}


class Sampler(threading.Thread):
    """Polls /proc every 500 ms and attributes the deltas to whichever span is open."""

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.spans = {}
        self.cur = None

    def open_span(self, name):
        with self.lock:
            self.cur = {"name": name, "t0": time.time(), "pts": [], "c": cpu_sample(),
                        "d": disk_sample()}
            self.spans[name] = self.cur

    def close_span(self, name):
        with self.lock:
            if self.cur and self.cur["name"] == name:
                self.cur = None

    def run(self):
        while True:
            with self.lock:
                span = self.cur
            if span is not None:
                span["pts"].append((time.time(), cpu_sample(), disk_sample()))
            time.sleep(0.5)

    def report(self, name):
        span = self.spans.get(name)
        if not span or len(span["pts"]) < 2:
            return {}
        first, last = span["pts"][0], span["pts"][-1]
        secs = max(1.0, last[0] - span["t0"])
        dc = {k: max(0, last[1][k] - span["c"][k]) for k in ("user", "sys", "iowait", "total")}
        dd = {k: max(0, last[2][k] - span["d"][k]) for k in ("rs", "rsec", "wsec")}
        tot = max(1, dc["total"])
        return {"span_s": round(secs, 1),
                "cpu_pct": round(100.0 * (dc["user"] + dc["sys"]) / tot, 1),
                "sys_pct": round(100.0 * dc["sys"] / tot, 1),
                "iowait_pct": round(100.0 * dc["iowait"] / tot, 1),
                "rd_MBps": round(dd["rsec"] * 512.0 / 2 ** 20 / secs, 1),
                "rd_iops": round(dd["rs"] / secs, 1)}


def drain():
    """Flush the engine log through the watcher so row `ts` stays within ~1 s of the window.

    The watcher stamps rows with the drain time - the engine's lines carry no timestamp - so filtering
    rows by a wall-clock span is only honest while something drains continuously.  DT_QUIET is silent.
    """
    try:
        subprocess.run(["python3.11", WATCH], timeout=120, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, env=dict(os.environ, DT_QUIET="1"))
    except Exception:
        pass


def drainer(stop):
    while not stop.is_set():
        drain()
        stop.wait(1.0)


def rows_between(a, b):
    out = []
    try:
        with open(ROWS, errors="ignore") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if a - 2 <= r.get("ts", 0) <= b + 2 and 0 < r.get("mswin", 0) < 60000:
                    out.append(r)
    except FileNotFoundError:
        pass
    return out


def med(rows, k):
    v = [r[k] for r in rows if r.get(k) is not None]
    return st.median(v) if v else float("nan")


def engine_lines(since_off):
    """Numeric fields of the engine's own lines since a byte offset; the text is never returned."""
    req = io = None
    try:
        with open(ENGINE, "rb") as f:
            f.seek(since_off)
            blob = f.read().decode("utf-8", "replace")
    except Exception:
        return req, io
    for m in REQ_RE.finditer(blob):
        req = {"prompt_tokens": int(m.group(1)), "reused": int(m.group(2)),
               "read": int(m.group(3)), "read_ms": int(m.group(4)), "pp_toks": float(m.group(5))}
    for m in IO_RE.finditer(blob):
        num = lambda s: float(s.replace(",", ""))
        io = {"os_MB": num(m.group(1)), "major_faults": int(num(m.group(2))),
              "expert_MB": num(m.group(3)), "cache_held_MB": num(m.group(4))}
    return req, io


def sign_test(d):
    n = len(d)
    if not n:
        return 1.0
    k = min(sum(1 for x in d if x > 0), sum(1 for x in d if x < 0))
    return min(1.0, 2.0 * sum(math.comb(n, i) for i in range(k + 1)) / 2.0 ** n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--streams", type=int, default=4)
    ap.add_argument("--deep-words", type=int, default=42000)
    ap.add_argument("--interf-words", type=int, default=48500)
    ap.add_argument("--decode-tokens", type=int, default=2400)
    ap.add_argument("--settle", type=float, default=6.0)
    ap.add_argument("--ctl-span", type=float, default=80.0)
    ap.add_argument("--port", default=os.environ.get("PORT", "8091"))
    a = ap.parse_args()
    if not KEY:
        raise SystemExit("STRATA_API_KEY missing from the environment - source /etc/profile.d/env.sh")
    url = "http://127.0.0.1:%s/v1/chat/completions" % a.port

    sampler = Sampler()
    sampler.start()
    stop = threading.Event()
    threading.Thread(target=drainer, args=(stop,), daemon=True).start()

    print("start %s port=%s streams=%d rounds=%d deep_words=%d interf_words=%d ctl_span=%.0f" % (
        time.strftime("%F %T"), a.port, a.streams, a.rounds, a.deep_words, a.interf_words,
        a.ctl_span), flush=True)

    # Build the resident sessions once: each starts as a cold ~131K prefill, then serves continuations.
    sessions = []
    for i in range(a.streams):
        text = uniq_text(a.deep_words, "setup-%d-%d" % (os.getpid(), i))
        t0, w = plain(url, [{"role": "user", "content": text}])
        sessions.append([{"role": "user", "content": text}, {"role": "assistant", "content": "ok"}])
        print("  session %d built wall=%.1f s" % (i, w), flush=True)

    cells = []
    for rnd in range(1, a.rounds + 1):
        iprompt = uniq_text(a.interf_words, "intf-%d-%d" % (rnd, os.getpid()))
        imsgs = [{"role": "user", "content": iprompt}]
        # Odd rounds take the floor first, even rounds last: the residency warmup inside a round then
        # shows up as disagreement between the two orderings instead of as an arm effect.
        for label in (["CTL", "I1", "I2"] if rnd % 2 else ["I1", "I2", "CTL"]):
            th, res = [None] * a.streams, [None] * a.streams
            t_start = time.time()
            for i in range(a.streams):
                res[i] = []
                th[i] = threading.Thread(target=streamed,
                                         args=(url, sessions[i] + [{"role": "user",
                                                                    "content": "continue"}],
                                               a.decode_tokens, res[i]))
                th[i].start()
                time.sleep(0.05)
            time.sleep(a.settle)

            log_off = os.path.getsize(ENGINE)
            span = "r%d-%s" % (rnd, label)
            sampler.open_span(span)
            req_line = io_line = None
            if label == "CTL":
                t0 = time.time()
                time.sleep(a.ctl_span)
                t1 = time.time()
                wall = a.ctl_span
            else:
                t0, wall = plain(url, imsgs, {"temperature": 0.0, "strata_checkpoint": False})
                t1 = time.time()
                time.sleep(1.0)
                req_line, io_line = engine_lines(log_off)
            sampler.close_span(span)
            for i in range(a.streams):
                th[i].join()
            drain()

            rr = rows_between(t0, t1)
            inside = []
            for stamps in res:
                if not stamps:
                    continue
                n = sum(1 for s in stamps if t0 <= s <= t1)
                s2 = min(t1, stamps[-1]) - max(t0, stamps[0])
                if s2 > 1:
                    inside.append(n / s2)
            cell = {"round": rnd, "arm": label, "n_rows": len(rr),
                    "cpu": round(med(rr, "cpu"), 2), "mswin": round(med(rr, "mswin"), 2),
                    "gpu_reach": round(med(rr, "gpu_reach"), 2),
                    "verify": round(med(rr, "verify"), 2),
                    "host": round(med(rr, "host"), 2),
                    "tg_in_span": round(st.median(inside), 2) if inside else 0.0,
                    "agg_in_span": round(sum(inside), 2), "streams_reporting": len(inside),
                    "interf_wall": round(wall, 1),
                    "cell_wall": round(time.time() - t_start, 1)}
            cell.update(sampler.report(span))
            if req_line:
                cell["req"] = req_line
            if io_line:
                cell["io"] = io_line
            cells.append(cell)
            print(json.dumps(cell, sort_keys=True), flush=True)
            with open(CELLS, "a") as f:
                f.write(json.dumps(cell) + "\n")

    stop.set()
    print("\n%-4s %-3s %8s %8s %8s %8s %7s %7s %8s %8s %9s" % (
        "rnd", "arm", "cpu", "mswin", "reach", "wall", "tg/str", "agg", "iowait", "rdMBps",
        "faults"), flush=True)
    for c in cells:
        print("%-4d %-3s %8.1f %8.1f %8.2f %8.1f %7.1f %7.1f %8.1f %8.1f %9s" % (
            c["round"], c["arm"], c["cpu"], c["mswin"], c["gpu_reach"], c["interf_wall"],
            c["tg_in_span"], c["agg_in_span"], c.get("iowait_pct", float("nan")),
            c.get("rd_MBps", float("nan")), (c.get("io") or {}).get("major_faults", "-")),
            flush=True)

    by = {}
    for c in cells:
        by.setdefault(c["round"], {})[c["arm"]] = c

    def paired(metric, arm):
        d = [row[arm].get(metric, float("nan")) - row["CTL"].get(metric, float("nan"))
             for row in (by[r] for r in sorted(by)) if "CTL" in row and arm in row]
        d = [x for x in d if x == x]
        return {"n": len(d), "med": st.median(d), "diffs": d, "p": sign_test(d)} if d else None

    print("\npaired vs CTL within the same round (median delta, per-round diffs, sign-test p):",
          flush=True)
    for metric in ("cpu", "mswin", "verify", "gpu_reach", "tg_in_span", "agg_in_span", "iowait_pct",
                   "rd_MBps"):
        for arm in ("I1", "I2"):
            r = paired(metric, arm)
            if r:
                print("  %-11s %-3s n=%d med %+.2f diffs %s p=%.3f" % (
                    metric, arm, r["n"], r["med"], " ".join("%+.2f" % x for x in r["diffs"]),
                    r["p"]), flush=True)

    print("\nstorage share of the damage (how much of I1's cost survives into I2, per round):",
          flush=True)
    for r in sorted(by):
        row = by[r]
        if not all(k in row for k in ("CTL", "I1", "I2")):
            continue
        line = ["  r%d" % r]
        for metric in ("cpu", "iowait_pct", "rd_MBps"):
            c, i1, i2 = (row[k].get(metric, float("nan")) for k in ("CTL", "I1", "I2"))
            if i1 == c or i1 != i1:
                line.append("%s=na" % metric)
            else:
                line.append("%s total%+.1f rest%+.1f (%.0f%% warm)" % (
                    metric, i1 - c, i2 - c, 100.0 * (i2 - c) / (i1 - c)))
        print(" ".join(line), flush=True)

    print("\nstorage evidence per interference cell:", flush=True)
    for c in cells:
        if c["arm"] == "CTL":
            continue
        io, q = c.get("io") or {}, c.get("req") or {}
        print("  r%d %s os_MB=%s faults=%s expert_MB=%s cache_held_MB=%s prompt=%s read_ms=%s "
              "pp=%s" % (c["round"], c["arm"], io.get("os_MB", "-"), io.get("major_faults", "-"),
                         io.get("expert_MB", "-"), io.get("cache_held_MB", "-"),
                         q.get("prompt_tokens", "-"), q.get("read_ms", "-"), q.get("pp_toks", "-")),
              flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()

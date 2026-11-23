#!/usr/bin/env python3.11
"""Per-card work census: does an imbalance cost wall time, or only move joules between cards?

Samples utilization.gpu / power.draw / clocks.sm at 200 ms while driving the lane through fixed
phases (idle -> 2 concurrent mid-window cold prefills -> the same two serially -> N deep cold
prefills -> N-way decode -> single-stream decode), then `workcensus-sum.py` turns the CSV into
per-card busy/util/power/**integrated joules** plus a co-busy histogram per phase.

Why energy: a layer split is a pipeline, not a pool (DEVLOG H.33).  Rebalancing the stages evened
every duty cycle and changed nothing else, because one 131K cold prefill costs the same ~17.1 kJ in
either geometry.  If total work is invariant, spreading consumers cannot raise the rate - the wall is
upstream of compute.  Never conclude from busy% alone: 4-way decode sat at 78-85 % busy, 22-38 % util
and 284 W for the whole box.

Runs traffic, so the lane must be quiet (lesson 23) and the disk cold tier quiesced, or mark the
phase as contaminated.  Numbers and durations only - never any model text.

Usage (detached, never from a bare interactive ssh):
  setsid nohup bash ac922/scripts/workcensus-arm.sh <layer-split> <nonce> \
      > /data/work/census-<nonce>.log 2>&1 < /dev/null &
  python3.11 ac922/scripts/workcensus-sum.py <nonce>

Env: CARDS (0,1,3,4) DEEPWORDS (42000 ~=131K) MIDWORDS (15000 ~=53K) NSESS (4)
     DECODETOK (240) WCDIR (/data/work)
"""

KEY = os.environ.get("STRATA_API_KEY", "")
URL = "http://127.0.0.1:8091/v1/chat/completions"
CARDS = os.environ.get("CARDS", "0,1,3,4")
DEEPW = int(os.environ.get("DEEPWORDS", "42000"))
MIDW = int(os.environ.get("MIDWORDS", "15000"))
NS = int(os.environ.get("NSESS", "4"))
DTOK = int(os.environ.get("DECODETOK", "240"))
NONCE = sys.argv[1] if len(sys.argv) > 1 else "m2"
WCDIR = os.environ.get("WCDIR", "/data/work")
CSV = WCDIR + "/workcensus-" + NONCE + ".csv"
EVT = WCDIR + "/workcensus-" + NONCE + "-evt.txt"
W = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike "
     "november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu").split()


def uniq_tokens(n, salt):
    v = zlib.crc32(salt.encode()) & 0x7FFFFFFF
    out = []
    for i in range(n):
        v = (v * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(W[(v + i * 7) % len(W)])
        if i % 4 == 3:
            out.append(str((v % 999983) + 1000))
    return " ".join(out)


def call(messages, mxt, stream=False):
    body = json.dumps({"model": "strata", "messages": messages, "max_tokens": mxt,
                       "temperature": 0, "stream": stream}).encode()
    r = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json",
                                                       "Authorization": "Bearer " + KEY})
    return urllib.request.urlopen(r, timeout=1800)


def run_plain(messages, mxt):
    t0 = time.time()
    with call(messages, mxt) as f:
        json.loads(f.read())
    return time.time() - t0


def run_stream(messages, mxt):
    t0 = time.time()
    ts = []
    with call(messages, mxt, True) as f:
        for raw in f:
            if raw.startswith(b"data:") and b"[DONE]" not in raw:
                ts.append(time.time() - t0)
    return ts, time.time() - t0


fevt = open(EVT, "w")


def mark(name, t0, t1, extra=""):
    fevt.write(name + "\t" + repr(t0) + "\t" + repr(t1) + "\t" + extra + "\n")
    fevt.flush()
    print(name + ": " + extra, flush=True)


def concurrent(texts, mxt=1):
    w = [None] * len(texts)
    d = [0.0] * len(texts)

    def go(i):
        d[i] = run_plain([{"role": "user", "content": texts[i]}], mxt)
    t0 = time.time()
    th = [threading.Thread(target=go, args=(i,)) for i in range(len(texts))]
    for t in th:
        t.start()
        time.sleep(0.05)
    for t in th:
        t.join()
    return time.time() - t0, d


smi = open(CSV, "w")
proc = subprocess.Popen(["nvidia-smi", "--id=" + CARDS,
                         "--query-gpu=timestamp,index,utilization.gpu,power.draw,clocks.sm",
                         "--format=csv,noheader,nounits", "-lms", "200"],
                        stdout=smi, stderr=subprocess.DEVNULL)


def log(msg):
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


try:
    time.sleep(10)
    mark("idle", time.time() - 10, time.time())

    pair = [uniq_tokens(MIDW, NONCE + "-p%d" % i) for i in (0, 1)]
    span, durs = concurrent(pair)
    mark("pf2par", time.time() - span, time.time(),
         "2 concurrent 53K prefills: span %.1f s  each %s" % (span, " ".join("%.1f" % d for d in durs)))
    time.sleep(2)
    t0 = time.time()
    s1 = run_plain([{"role": "user", "content": pair[0]}], 1)
    s2 = run_plain([{"role": "user", "content": pair[1]}], 1)
    mark("pf2seq", t0, time.time(),
         "same 2 re-run (cached): %.1f + %.1f = %.1f s" % (s1, s2, s1 + s2))
    t0 = time.time()
    a = uniq_tokens(MIDW, NONCE + "-q0")
    b = uniq_tokens(MIDW, NONCE + "-q1")
    w1 = run_plain([{"role": "user", "content": a}], 1)
    w2 = run_plain([{"role": "user", "content": b}], 1)
    mark("pf2coldseq", t0, time.time(),
         "2 serial 53K prefills: %.1f + %.1f = %.1f s" % (w1, w2, w1 + w2))

    sess = []
    for i in range(NS):
        text = uniq_tokens(DEEPW, NONCE + "-s%d" % i)
        t0 = time.time()
        d = run_plain([{"role": "user", "content": text}], 1)
        mark("prefill%d" % i, t0, time.time(), "131K cold prefill wall %.1f s" % d)
        sess.append([{"role": "user", "content": text}, {"role": "assistant", "content": "ok"}])

    th = [None] * NS
    curves = [None] * NS

    def worker(i):
        curves[i] = run_stream(sess[i] + [{"role": "user", "content": "continue"}], DTOK)
    t0 = time.time()
    for i in range(NS):
        th[i] = threading.Thread(target=worker, args=(i,))
        th[i].start()
        time.sleep(0.05)
    for i in range(NS):
        th[i].join()
    span = time.time() - t0
    tot = sum(len(curves[i][0]) for i in range(NS))
    per = " ".join("%.1f" % (len(curves[i][0]) / curves[i][1]) for i in range(NS))
    mark("decode" + str(NS), t0, time.time(),
         "agg %.1f tok/s | per-stream %s" % (tot / span, per))

    time.sleep(3)
    t0 = time.time()
    ts1, d1 = run_stream(sess[0] + [{"role": "user", "content": "continue"}], DTOK)
    mark("decode1", t0, time.time(), "single-stream %.1f tok/s in %.1f s" % (len(ts1) / d1, d1))
finally:
    proc.terminate()
    smi.close()
    fevt.close()
    subprocess.run(["pkill", "-f", "nvidia-smi --id=" + CARDS], stderr=subprocess.DEVNULL)
log("DONE")

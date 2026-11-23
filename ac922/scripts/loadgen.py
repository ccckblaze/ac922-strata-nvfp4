#!/usr/bin/env python3
"""Deterministic co-tenant simulator for the AC922 lane.

The operator's NNLC job array is a *training* workload: several threads doing tensor math over
multi-gigabyte buffers.  Its effect on our lane cannot be studied by waiting for it to arrive, so this
generator reproduces the two axes separately and repeatably:

  --cpu N      N threads of pure arithmetic on a small buffer (SMT / scheduler contention only)
  --bw N       N threads streaming a --bw-mib buffer (memory-bandwidth and, on node-pinned runs, the
               cross-socket NVLink path as well)
  --node K      taskset the workers to one NUMA node's CPU list (omit = let the scheduler choose)

It never touches the GPUs.  Usage: loadgen.py --cpu 8 --bw 8 --bw-mib 256 --seconds 300 [--node 8]
"""
import argparse, os, subprocess, sys, threading, time

BUF = memoryview(bytearray(0))


def cpu_worker(stop):
    x = 1.0000001
    while time.time() < stop:
        for _ in range(20000):
            x = x * 1.0000001 + 1e-9
    return x


def bw_worker(stop, src, dst):
    n = len(src)
    i = 0
    while time.time() < stop:
        dst[:] = src
        i += 1
        if i % 512 == 0:
            time.sleep(0)


def node_cpus(node):
    path = "/sys/devices/system/node/node%d/cpulist" % node
    txt = open(path).read().strip()
    out = []
    for part in txt.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cpu", type=int, default=0)
    ap.add_argument("--bw", type=int, default=0)
    ap.add_argument("--bw-mib", type=int, default=256)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--node", type=int, default=-1)
    ap.add_argument("--pidfile", default="/tmp/loadgen.pid")
    a = ap.parse_args()
    stop = time.time() + a.seconds
    if a.node >= 0:
        cpus = node_cpus(a.node)
        os.sched_setaffinity(0, set(cpus))
        print("loadgen: pinned to node %d (%d CPUs: %d-%d)" % (a.node, len(cpus), cpus[0], cpus[-1]), flush=True)
    threads = []
    src = bytearray(1)
    if a.bw:
        src = bytes(range(256)) * (a.bw_mib * 1048576 // 256)
    for i in range(a.cpu):
        t = threading.Thread(target=cpu_worker, args=(stop,), daemon=True)
        t.start()
        threads.append(t)
    for i in range(a.bw):
        dst = bytearray(len(src))
        t = threading.Thread(target=bw_worker, args=(stop, src, dst), daemon=True)
        t.start()
        threads.append(t)
    open(a.pidfile, "w").write(str(os.getpid()))
    t0 = time.time()
    while time.time() < stop:
        time.sleep(min(5.0, stop - time.time()))
        print("loadgen alive %.0f s (cpu=%d bw=%d x %d MiB node=%d)" % (
            time.time() - t0, a.cpu, a.bw, a.bw_mib, a.node), flush=True)
    print("loadgen done after %.0f s" % (time.time() - t0), flush=True)


main()

#!/usr/bin/env python3.11
# C-phase boot-level paired A/B harness (profile / adapt / trim tickets).
# Boots one named arm, then runs the fixed measurement sequence; prints numbers only.
import json, os, re, subprocess, sys, time, urllib.request

REPO   = "/data/work/ac922-strata-nvfp4"
CFG    = REPO + "/ac922/configs/strata-nvfp4-final.json"
DEFBIN = "/data/work/strata-nvfp4-src/data/expert-profile.bin"
LEARN  = "/data/work/expert-profile-learned.bin"
CODER  = REPO + "/data/expert-profile-coder.bin"
RUNSH  = "/root/strata-nvfp4-run.sh"
OUTD   = "/data/work/note_c17"
URL    = "http://127.0.0.1:8091"
KEY    = os.environ.get("STRATA_API_KEY", "")

ARMS = {
    "CTL_A": {}, "CTL_B": {},
    "P1":    {"profile": LEARN},
    "C1":    {"profile": CODER},
    "A0":    {"adapt0": True},
    "PT1":   {"profile": LEARN, "adapt0": True},
    "T1":    {"trim": True},
    "P1T":   {"profile": LEARN, "adapt0": True, "trim": True},
    "RESTORE": {"restore": True},
}

def mutate(spec):
    if spec.get("restore") and os.path.exists(CFG + ".orig"):
        subprocess.run(["cp", CFG + ".orig", CFG], check=True); return
    if not os.path.exists(CFG + ".orig"):
        subprocess.run(["cp", CFG, CFG + ".orig"], check=True)
    d = json.load(open(CFG + ".orig")); args = d["args"]
    out = []
    i = 0
    while i < len(args):
        if args[i] == "--expert-profile":
            out += [args[i], spec.get("profile", DEFBIN)]; i += 2; continue
        if args[i] == "--adapt-every":
            i += 2; continue
        if args[i] == "--trim-stage-weights":
            i += 1; continue
        out.append(args[i]); i += 1
    if "profile" in spec and "--expert-profile" not in out:
        out += ["--expert-profile", spec["profile"]]
    if spec.get("adapt0"): out += ["--adapt-every", "0"]
    if spec.get("trim"):   out += ["--trim-stage-weights"]
    d["args"] = out
    json.dump(d, open(CFG, "w"), indent=1)

def sh(cmd, to=None):
    # Redirect to a file, not a captured pipe: run.sh start leaves daemonized
    # grandchildren alive, and a captured pipe never sees EOF, hanging us there.
    out = open(OUTD + "/sh.log", "ab")
    subprocess.run(["bash", "-lc", cmd], stdout=out, stderr=subprocess.STDOUT, timeout=to)
    out.close()
    return ""

def health():
    req = urllib.request.Request(URL + "/health", headers={"Authorization": "***" + KEY})
    try:
        return urllib.request.urlopen(req, timeout=5).status == 200
    except Exception:
        return False

def boot(tag, spec):
    mutate(spec)
    sh(f"{RUNSH} stop; sleep 3")
    t0 = time.time()
    sh(f"{RUNSH} start")
    while time.time() - t0 < 1800:
        if health(): break
        time.sleep(15)
    else:
        print(tag, "BOOT-TIMEOUT", flush=True); sys.exit(2)
    print(tag, "boot ok %.0fs" % (time.time() - t0), flush=True)

def run(name, cmd, env, log):
    e = dict(os.environ); e.update(env)
    r = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=3600, env=e)
    open(log, "w").write(r.stdout + "\n" + r.stderr)
    print(name, "done", flush=True)

def measure(tag):
    E = {"SB_URL": URL, "SB_ENG": "/data/champion/strata-nvfp4-engine.log"}
    S = "cd " + REPO + "/ac922/eval && python3.11"
    O = OUTD + "/" + tag
    run(tag + ".det",  S + " sbench.py det", E, O + ".det")
    run(tag + ".long", S + " sbench.py long", E, O + ".long")
    run(tag + ".conc", S + " sbench.py conc 8000 6", E, O + ".conc8k")
    try:
        m = sh(f"curl -s -H 'Authorization: Bearer {KEY}' {URL}/metrics")
        g = re.findall(r"(hit[a-z_]*|resident[a-z_]*)[ =][\d.]+", m)[:8]
        print(tag, "metrics", g, flush=True)
    except Exception:
        pass

if __name__ == "__main__":
    tag = sys.argv[1]
    os.makedirs(OUTD, exist_ok=True)
    boot(tag, ARMS[tag]); measure(tag)
    print(tag, "ARM-END", flush=True)

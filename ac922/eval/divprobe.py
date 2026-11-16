#!/usr/bin/env python3.11
"""Divergence probe: fixed cells at two prompt depths, hashed.

Path control is the whole point of the 2026-11-16 rewrite.  Greedy output on this lane depends on how
much of a prompt was re-prefilled versus restored from a parked chain (measured same-boot: `reused=0`
cold, `reused=36864` partial and full relay each give a different answer for the same 54K prompt), so a
fixture that reuses one prompt text across runs silently changes path between runs and no longer measures
the numeric path -- it measures the parking history of the box.  That is why a check issued right after
a baseline in the same boot reported FAIL on its *earliest* cells.

Fix: the prompt text is *fixed per cell* (never salted with the run tag -- that made two "same" cells
two different prompts), and each depth is primed with one throwaway request so the measured cells all
run at the relay depth a live lane actually serves at.  Each row records the exact `reused` count, and
`qualgate` only compares a cell against a REF cell taken at the same depth; a depth change is a path
change, not a numeric regression.  Cold-path determinism is covered separately by the det fixture.

Emits hashes and counters only -- never model text.

env: SB_URL, SB_ENG (engine self-report log, for path capture), STRATA_API_KEY.  usage: divprobe.py TAG
"""
import hashlib,json,os,re,sys,time,urllib.error,urllib.request

URL=os.environ.get("SB_URL","http://127.0.0.1:8091")
ENG=os.environ.get("SB_ENG","/data/champion/strata-nvfp4-engine.log")
KEY=os.environ.get("STRATA_API_KEY","")
MODEL=os.environ.get("SB_MODEL","qwen3.8-flash-next")
FILL="Reimagine a lighthouse keeper logging nightly waves and distant ships; "
PAT=re.compile(r"prompt (\d+) tokens = (\d+) reused")

def post(path,payload,timeout=7200):
    d=json.dumps(payload).encode()
    hd={"Content-Type":"application/json"}
    if KEY: hd["Authorization"]="Bearer "+KEY
    r=urllib.request.Request(URL+path,data=d,headers=hd)
    try:
        with urllib.request.urlopen(r,timeout=timeout) as x: return json.loads(x.read())
    except urllib.error.HTTPError as e:
        if e.code in (401,403):
            raise SystemExit("GATE-ERROR auth-%d: export STRATA_API_KEY before running the gate"%e.code)
        raise

def ask(text,max_new):
    r=post("/v1/chat/completions",{"model":MODEL,"messages":[{"role":"user","content":text}],"temperature":0,"max_tokens":max_new})
    msg=r["choices"][0]["message"]
    return hashlib.sha256(json.dumps(msg,sort_keys=True).encode()).hexdigest()[:12], r.get("usage",{}).get("prompt_tokens")

def reused(pt):
    """How far back this request had to re-read, read from the engine's own line for that ptok."""
    if not os.path.exists(ENG): return None
    v=None
    try:
        with open(ENG,"rb") as f:
            f.seek(max(0,os.path.getsize(ENG)-400000)); tail=f.read().decode("utf-8","replace")
        for line in tail.splitlines():
            m=PAT.search(line)
            if m and int(m.group(1))==pt: v=int(m.group(2))
    except OSError:
        return None
    return v

def klass(re_):
    if re_ is None: return "unknown"
    return "cold" if re_==0 else ("relay" if re_<512 else "partial")

def build(salt,target):
    u=post("/v1/chat/completions",{"model":MODEL,"messages":[{"role":"user","content":FILL*20}],"temperature":0,"max_tokens":1})
    per=u["usage"]["prompt_tokens"]/20.0
    return salt+" "+FILL*max(1,int(target/per))

tag=sys.argv[1] if len(sys.argv)>1 else "A"
PRIME=int(os.environ.get("SB_PRIME","1"))
for tgt in (64000,131000):
    t=build("QPRB20261116-%d"%tgt,tgt)      # fixed per cell: identical prompt across every gate run
    if PRIME: ask(t,1)                      # one cold pass parks the chain; measured cells then share its depth
    for L in (8,32,128,512):
        h,pt=ask(t,L)
        ru=reused(pt)
        print(json.dumps({"run":tag,"tgt":tgt,"L":L,"h":h,"ptok":pt,"reused":ru,"path":klass(ru)}),flush=True)
print("PROBE-DONE "+tag)

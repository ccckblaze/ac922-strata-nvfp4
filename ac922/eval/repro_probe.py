#!/usr/bin/env python3.11
"""Reproducibility floor for the lane: how far does a greedy (temperature 0) generation stay
bit-identical when the same request is replayed against the same running engine?

Why this exists (2026-11-16): the hash gates in qualgate.sh assume replay equality.  On this lane a
30K-token prompt replayed four times produced 3 distinct 128-token answers and 4 distinct
512-token answers, with the first differing character at index 8 in half of the pairs.  So an exact
hash is a usable invariant only at short horizons; anything longer has to be judged by this probe
(distinct count, first-divergence position) plus the aggregate accuracy anchors, not by equality.

Emits numbers, indices and hashes only -- never model text.

env: SB_URL, STRATA_API_KEY.  usage: repro_probe.py [TAG] [PROMPT_TOK] [N]
"""
import itertools,json,os,sys,urllib.request,urllib.error

URL=os.environ.get("SB_URL","http://127.0.0.1:8091")
KEY=os.environ.get("STRATA_API_KEY","")
MODEL=os.environ.get("SB_MODEL","qwen3.8-flash-next")
FILL="Reimagine a lighthouse keeper logging nightly waves and distant ships; "
TAG=sys.argv[1] if len(sys.argv)>1 else "rp"
PTOK=int(sys.argv[2]) if len(sys.argv)>2 else 30000
N=int(sys.argv[3]) if len(sys.argv)>3 else 4
HORIZONS=[int(x) for x in (sys.argv[4].split(",") if len(sys.argv)>4 else ["32","128","512"])]

def post(payload,timeout=7200):
    hd={"Content-Type":"application/json"}
    if KEY: hd["Authorization"]="Bearer "+KEY
    r=urllib.request.Request(URL+"/v1/chat/completions",data=json.dumps(payload).encode(),headers=hd)
    try:
        with urllib.request.urlopen(r,timeout=timeout) as x: return json.loads(x.read())
    except urllib.error.HTTPError as e:
        if e.code in (401,403):
            raise SystemExit("GATE-ERROR auth-%d: export STRATA_API_KEY before running the probe"%e.code)
        raise

def ask(text,L):
    r=post({"model":MODEL,"messages":[{"role":"user","content":text}],"temperature":0,"max_tokens":L})
    ch=r["choices"][0]; m=ch["message"]
    body=m.get("content") or m.get("reasoning_content") or ""
    return body,ch.get("finish_reason"),r["usage"]["completion_tokens"]

cal=post({"model":MODEL,"messages":[{"role":"user","content":FILL*20}],"temperature":0,"max_tokens":1})
per=cal["usage"]["prompt_tokens"]/20.0
text="REPRO20261116 "+FILL*max(1,int(PTOK/per))
for L in HORIZONS:
    runs=[ask(text,L) for _ in range(N)]
    bodies=[b for b,_,_ in runs]
    dist=len(set(bodies))
    pairs=[]
    for a,b in itertools.combinations(range(len(bodies)),2):
        x,y=bodies[a],bodies[b]; n=min(len(x),len(y)); k=0
        while k<n and x[k]==y[k]: k+=1
        pairs.append(k)
    full=[k for (a,b),k in zip(list(itertools.combinations(range(N),2)),pairs)
          if k>=min(len(bodies[a]),len(bodies[b]))]
    print(json.dumps({"run":TAG,"ptok_target":PTOK,"L":L,"n":N,"distinct":dist,
                      "identical_pairs":len(full),"firstdiff_chars":sorted(pairs),
                      "chars":sorted(len(b) for b in bodies)}),flush=True)
print("REPRO-END "+TAG,flush=True)

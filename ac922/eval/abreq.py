#!/usr/bin/env python3.11
"""Request-level paired A/B harness: arms are per-request fields, rounds alternate, verdict is the
paired median plus a sign test.  Numbers, hashes and counters only -- never model text.

Why this harness exists in this shape
-------------------------------------
`--spec-min-p`, `pcie_frac` and `aux_cpus` are accepted *per request* (`sampling.strata_tune`), so a
whole sweep runs inside one boot and needs no restarts.  Two rules are baked in, both learned the
hard way on 2026-11-16:

1. **Depth is primed.**  Output and speed depend on how much of the prompt is re-prefilled versus
   restored, so every arm of a task runs against a conversation that has already been parked once;
   the harness records each row's `reused` and refuses to compare arms measured at different depths.
2. **The decode budget is part of the shape.**  A 64-token completion on a 250K prompt measures the
   drafting ramp, not steady-state decode: at deep context tg falls from ~60 (64 tokens) to ~31
   (700 tokens) on this lane.  Operators live in the long-decode regime, so `deep` defaults there.

Arms come from AB_ARMS, `NAME:key=value[;key=value],...` with keys in
(spec_min_p, pcie_frac, aux_cpus, prefill_pipe); an empty spec is the control.

env: SB_URL, SB_ENG, STRATA_API_KEY, AB_OUT, AB_TASK, SB_TARGET, SB_GEN, SB_ROUNDS, AB_ARMS, AB_BASE
usage: abreq.py [task]   (tasks: deep | t2)
"""
import hashlib,json,math,os,re,statistics,sys,threading,time,urllib.error,urllib.request

URL=os.environ.get("SB_URL","http://127.0.0.1:8091")
ENG=os.environ.get("SB_ENG","/data/champion/strata-nvfp4-engine.log")
KEY=os.environ.get("STRATA_API_KEY","")
MODEL=os.environ.get("SB_MODEL","qwen3.8-flash-next")
FILL="Reimagine a lighthouse keeper logging nightly waves and distant ships; "
OUTF=os.environ.get("AB_OUT","/data/work/abreq.jsonl")
ROUNDS=int(os.environ.get("SB_ROUNDS","4"))
IDLE_S=int(os.environ.get("AB_IDLE_WAIT","120"))
TARGET=int(os.environ.get("SB_TARGET","120000"))
GEN=int(os.environ.get("SB_GEN","400"))
PAT=re.compile(r"prompt (\d+) tokens = (\d+) reused \+ ([\d,]+)(?: of [\d,]+)? read in (\d+) ms \(([\d.]+) tok/s\), (\d+) generated in (\d+) ms \(([\d.]+) tok/s\), drafts accepted (\d+) of (\d+)")
KVP=re.compile(r"KV streaming: ([\d.]+)% of ([\d,]+) block reads hit VRAM, ([\d.]+) MiB read from RAM")
OFF=[os.path.getsize(ENG)]
LOCK=threading.Lock()
TUNE_KEYS=("spec_min_p","pcie_frac","aux_cpus","prefill_pipe")

def post(payload,timeout=7200):
    hd={"Content-Type":"application/json"}
    if KEY: hd["Authorization"]="Bearer "+KEY
    r=urllib.request.Request(URL+"/v1/chat/completions",data=json.dumps(payload).encode(),headers=hd)
    try:
        with urllib.request.urlopen(r,timeout=timeout) as x: return json.loads(x.read())
    except urllib.error.HTTPError as e:
        if e.code in (401,403): raise SystemExit("ABORT auth-%d: export STRATA_API_KEY first"%e.code)
        raise

def drain_upto(end):
    with LOCK:
        with open(ENG,"rb") as f:
            f.seek(OFF[0]); data=f.read(end-OFF[0]); OFF[0]=end
    return data.decode("utf-8","replace").splitlines()

def wait_row(pt,ct,deadline):
    """The engine writes the prefill row ("1 generated") while a request runs and the completion row
    ("N generated in ... tok/s") when it finishes, sometimes a moment after the HTTP response returns.
    Matching the first row measured the first token instead of the decode, which is how four arms of a
    sweep once reported 22-35 tok/s for identical work.  So: wait for the row whose generated count
    equals what the API reports, and never settle for an earlier one."""
    foreign=0; kv=None
    while time.time() < deadline:
        with LOCK:
            with open(ENG,"rb") as f:
                f.seek(OFF[0]); data=f.read().decode("utf-8","replace"); OFF[0]=os.path.getsize(ENG)
        best=None
        for line in data.splitlines():
            if "strata serve: prompt" in line:
                m=PAT.search(line)
                if not m: continue
                if int(m.group(1))!=pt:
                    foreign+=1; continue
                row={"reused":int(m.group(2)),"pp":float(m.group(5)),"gen":int(m.group(6)),
                     "tg":float(m.group(8)),"acc":(int(m.group(9)),int(m.group(10)))}
                if row["gen"]==ct: best=row if best is None or row["gen"]>=best["gen"] else best
            elif "KV streaming:" in line:
                k=KVP.search(line)
                if k: kv=(float(k.group(1)),float(k.group(3)))
        if best is not None:
            return best,foreign,kv
        # (foreign and kv accumulate across polls: a neighbour's rows seen while waiting still count)
        time.sleep(0.3)
    return None,0,None

def ask(text,max_new,extra=None,wait_idle_for=0):
    """One request.  `wait_idle_for` first requires the lane to be quiet (a 1-token canary that comes
    back fast), so a sample is not measured behind somebody else's deep turn."""
    if wait_idle_for: wait_idle(wait_idle_for)
    t0=time.time()
    req={"model":MODEL,"messages":[{"role":"user","content":text}],"temperature":0,"max_tokens":max_new}
    req.update(extra or {})
    r=post(req); wall=round(time.time()-t0,2)
    pt=r["usage"]["prompt_tokens"]; ct=r["usage"]["completion_tokens"]
    msg=r["choices"][0]["message"]
    h=hashlib.sha256(json.dumps(msg,sort_keys=True).encode()).hexdigest()[:12]
    row,foreign,kv=wait_row(pt,ct,time.time()+float(os.environ.get("AB_ROW_WAIT","20")))
    row=row or {}
    acc=row.get("acc")
    return {"ts":round(t0,1),"wall":wall,"ptok":pt,"ctok":ct,"gen":row.get("gen"),"reused":row.get("reused"),
            "pp":row.get("pp"),"tg":row.get("tg"),"acc":list(acc) if acc else None,
            "acc_rate":(round(acc[0]/acc[1],4) if acc and acc[1] else None),
            "kv_vram_hit_pct":kv[0] if kv else None,"foreign":foreign,"hash":h}

def wait_idle(max_wait_s):
    """A 1-token canary measures queue position: on a quiet lane it comes back in well under a second."""
    limit=time.time()+max_wait_s; best=None
    while time.time()<limit:
        t0=time.time()
        try: post({"model":MODEL,"messages":[{"role":"user","content":"ok"}],"temperature":0,"max_tokens":1},timeout=120)
        except Exception: pass
        best=round(time.time()-t0,2)
        if best < float(os.environ.get("AB_IDLE_CANARY_S","1.0")): return best
        time.sleep(2.0)
    return best

def build(target):
    per=None
    for _ in range(2):
        u=post({"model":MODEL,"messages":[{"role":"user","content":FILL*20}],"temperature":0,"max_tokens":1})
        per=u["usage"]["prompt_tokens"]/20.0 if per is None else (per+u["usage"]["prompt_tokens"]/20.0)/2
    return FILL*max(1,int(target/per))

def parse_arms(spec,base):
    arms={}
    for item in spec.split(","):
        if not item.strip(): continue
        name,_,kv=item.partition(":")
        d={}
        for pair in filter(None,kv.split(";")):
            k,_,v=pair.partition("=")
            if k not in TUNE_KEYS: raise SystemExit("ABORT unknown tune key %r (allowed: %s)"%(k,",".join(TUNE_KEYS)))
            v=v.strip()
            d[k]=(v if v in ("true","false") else (float(v) if "." in v or "e" in v.lower() else int(v)))
        arms[name.strip()]=({"strata_tune":d} if d else {})
    if not arms: raise SystemExit("ABORT no arms given in AB_ARMS")
    if base not in arms: raise SystemExit("ABORT AB_BASE %r not among arms %s"%(base,list(arms)))
    return arms

def sign_test(diffs):
    nz=[d for d in diffs if d!=0]; n=len(nz)
    if n==0: return 1.0
    pos=sum(1 for d in nz if d>0); k=min(pos,n-pos)
    return min(1.0,sum(math.comb(n,i) for i in range(k+1))/(2**(n-1)))

def summarize(rows,base):
    per={}
    for r in rows: per.setdefault(r["arm"],{}).setdefault(r["metric"],[]).append((r["round"],r["value"]))
    out=[]
    for arm,mets in per.items():
        for met,vals in mets.items():
            v=[x for _,x in vals]
            rec={"arm":arm,"metric":met,"n":len(v),"median":round(statistics.median(v),3)}
            if arm!=base and base in per and met in per[base]:
                rb=dict(per[base][met]); ra=dict(vals); both=sorted(set(rb)&set(ra))
                if both:
                    diffs=[ra[i]-rb[i] for i in both]; medb=statistics.median(rb[i] for i in both)
                    rec.update(n_pair=len(both),delta_med=round(statistics.median(diffs),3),
                               delta_pct=(round(100.0*statistics.median(diffs)/medb,2) if medb else None),
                               p_sign=round(sign_test(diffs),4))
            out.append(rec)
    return out

def run(task,corpus,maxnew,arms,base,rounds,prime=True):
    if prime:
        p=ask(corpus,8,{},wait_idle_for=IDLE_S)
        print("PRIME ptok=%s reused=%s (arms are measured at this depth)"%(p["ptok"],p["reused"]),flush=True)
        with LOCK: OFF[0]=os.path.getsize(ENG)
    rows=[]; fh=open(OUTF,"a")
    depths=set()
    for rd in range(1,rounds+1):
        order=list(arms) if rd%2 else list(reversed(arms))
        for arm in order:
            res=ask(corpus,maxnew,arms[arm],wait_idle_for=IDLE_S)
            fh.write(json.dumps({"task":task,"round":rd,"arm":arm,"res":res})+"\n"); fh.flush()
            if res["reused"] is not None: depths.add(res["reused"])
            for met in ("tg","wall","pp","acc_rate","kv_vram_hit_pct"):
                if res.get(met) is not None: rows.append({"arm":arm,"metric":met,"round":rd,"value":res[met]})
            print("R%d %-6s tg=%-6s acc=%-6s pp=%-8s reused=%-7s kvhit=%-6s foreign=%s wall=%s"%(
                  rd,arm,res["tg"],res["acc_rate"],res["pp"],res["reused"],res["kv_vram_hit_pct"],res["foreign"],res["wall"]),flush=True)
    fh.close()
    if len(depths)>1: print("NOTE depth varied across rows (%s): tg is not strictly comparable"%sorted(depths),flush=True)
    for s in summarize(rows,base): print("SUM "+json.dumps(s,sort_keys=True),flush=True)

def main():
    task=sys.argv[1] if len(sys.argv)>1 else os.environ.get("AB_TASK","deep")
    if not KEY: raise SystemExit("ABORT no STRATA_API_KEY")
    if not os.access(ENG,os.R_OK): raise SystemExit("ABORT unreadable engine log %s"%ENG)
    base=os.environ.get("AB_BASE","CTL")
    if task=="deep":
        arms=parse_arms(os.environ.get("AB_ARMS","CTL:,p55:spec_min_p=0.55,p85:spec_min_p=0.85,p95:spec_min_p=0.95,pc50:pcie_frac=0.5"),base)
        run("deep",build(TARGET),GEN,arms,base,ROUNDS)
    elif task=="t2":
        arms=parse_arms(os.environ.get("AB_ARMS","CTL:,p55:spec_min_p=0.55,p85:spec_min_p=0.85,p95:spec_min_p=0.95"),base)
        run("t2",build(int(os.environ.get("SB_SHORT","8000"))),GEN,arms,base,ROUNDS)
    else: raise SystemExit("unknown task %r (deep|t2)"%task)
    print("ABREQ-END "+task,flush=True)

if __name__=="__main__": main()

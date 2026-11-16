#!/usr/bin/env python3.11
"""restore-gap probe: cold floor / cold long prefill / warm resume-gap cost.

Reads only the engine's own self-report lines ("strata serve: prompt N tokens = R reused + G
read in T ms") and prints numbers; no model text leaves the process.  Use it to price the
resume grid: a continuation re-reads `gap = prompt - reused` tokens, and `reused` is pinned to a
checkpoint grid that tracks --prefill, not --prompt-cache-every (see DEVLOG phase H).

env: SB_URL (lane), SB_ENG (engine log to tail), SB_TAG, SB_MODEL, BASE_TOK, GAPS,
     COLD2=1 (repeat the cold base prompt to separate cold-read from cache-hit), SB_OUT.
"""
import json,os,re,time,hashlib,urllib.request
URL=os.environ.get("SB_URL","http://127.0.0.1:8092"); ENG=os.environ.get("SB_ENG","/data/champion/strata-nvfp4-dev-engine.log")
TAG=os.environ.get("SB_TAG","CTL"); MODEL=os.environ.get("SB_MODEL","qwen3.8-flash-next")
BASE_TOK=int(os.environ.get("BASE_TOK","96000")); GAPS=[int(x) for x in os.environ.get("GAPS","0 2048 6144 10240 15000").split()]
KEY=os.environ.get("STRATA_API_KEY",""); OFF=[os.path.getsize(ENG)]
FILL="Reimagine a lighthouse keeper logging nightly waves and distant ships; "
ALT="Document the migration routes of arctic terns across eleven seasonal storms and record "
def post(payload,timeout=900):
    r=urllib.request.Request(URL+"/v1/chat/completions",data=json.dumps(payload).encode(),
        headers={"Content-Type":"application/json","Authorization":"Bearer "+KEY})
    with urllib.request.urlopen(r,timeout=timeout) as x: return json.loads(x.read())
def newlines():
    with open(ENG,"rb") as f:
        f.seek(OFF[0]); data=f.read().decode("utf-8","replace"); OFF[0]=f.tell()
    return [l for l in data.splitlines() if "strata serve: prompt" in l]
PAT=re.compile(r"prompt (\d+) tokens = (\d+) reused \+ ([\d,]+)(?: of \d+)? read in (\d+) ms \(([\d.]+) tok/s\)")
def ask(text):
    t0=time.time(); u=post({"model":MODEL,"messages":[{"role":"user","content":text}],"temperature":0,"max_tokens":1})
    wall=time.time()-t0; pt=u.get("usage",{}).get("prompt_tokens")
    rec=None
    for l in newlines():
        m=PAT.search(l)
        if m and int(m.group(1))==pt: rec=m
    if rec is None: return {"ptok":pt,"wall":round(wall,2)}
    p,reu,rd,pms,pp=int(rec.group(1)),int(rec.group(2)),int(rec.group(3).replace(",","")),int(rec.group(4)),float(rec.group(5))
    return {"ptok":p,"reused":reu,"read":rd,"pms":pms,"pp_e":pp,"gap":p-reu,"wall":round(wall,2),
            "rate_gap":round(1000.0*rd/max(1,pms),1) if rd>0 else None}
def show(nm,r):
    line="%-12s ptok=%-7d reused=%-7d read=%-6d gap=%-6d pms=%-7dms pp_e=%8s wall=%5.1fs"%(nm,r.get("ptok",0),r.get("reused",0),r.get("read",0),r.get("gap",0),r.get("pms",0),r.get("pp_e"),r.get("wall",0))
    if r.get("rate_gap") is not None: line+="  read_rate=%s tok/s"%r["rate_gap"]
    print(line,flush=True); return r
u=show("calib",ask(FILL*20)); per=u["ptok"]/20.0
print("tokens per FILL unit=%.2f"%per,flush=True)
res={}
for i in range(3):
    res["floor%d"%i]=show("cold~128",ask("Seed %d alpha beta gamma: "%i + ALT*11))
n=int((BASE_TOK-40)/per); base=FILL*n
res["base_cold"]=show("cold_base",ask(base))
if os.environ.get("COLD2"): res["base_cold2"]=show("cold_base2",ask(base))
for g in GAPS:
    extra=ALT*max(1,int(g/(per*13))) if g>0 else ""
    res["gap%d"%g]=show("warm_g%d"%g,ask(base+extra if g>0 else base))
out=os.environ.get("SB_OUT",".")
json.dump({"tag":TAG,"ts":time.strftime("%F %T"),"per":per,"res":res},open(os.path.join(out,"rprobe-%s.json"%TAG),"w"))
print("DONE %s"%TAG,flush=True)

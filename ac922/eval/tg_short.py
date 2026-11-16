#!/usr/bin/env python3.11
"""short-window decode control: 8K prompt + 512 tokens x N rounds, tg/acceptance from self-report.

Pairs with resume_probe.py: geometry changes that buy resume gaps must not move this number, so
every arm is read as (gap cost, tg) together.  Prints numbers and a 12-hex digest of each reply
(determinism check), never the reply itself.

env: SB_URL, SB_ENG, SB_TAG, SB_MODEL, SB_N (rounds), SB_OUT.
"""
import json, os, re, sys, time, hashlib, statistics, urllib.request
URL=os.environ.get("SB_URL","http://127.0.0.1:8091"); ENG=os.environ.get("SB_ENG","/data/champion/strata-nvfp4-engine.log")
FILL="Reimagine a lighthouse keeper logging nightly waves and distant ships; "
MODEL=os.environ.get("SB_MODEL","qwen3.8-flash-next"); N=int(os.environ.get("SB_N","6")); TAG=os.environ.get("SB_TAG","nvfp4")
OFF=[0]
def post(p,timeout=7200):
    r=urllib.request.Request(URL+"/v1/chat/completions",data=json.dumps(p).encode(),headers={"Content-Type":"application/json", "Authorization": "Bearer " + os.environ.get("STRATA_API_KEY", "")})
    with urllib.request.urlopen(r,timeout=timeout) as x: return json.loads(x.read())
def ask(text,n):
    t0=time.time()
    r=post({"model":MODEL,"messages":[{"role":"user","content":text}],"temperature":0,"max_tokens":n})
    wall=round(time.time()-t0,2); msg=r["choices"][0]["message"]
    h=hashlib.sha256(json.dumps(msg,sort_keys=True).encode()).hexdigest()[:12]
    with open(ENG,"rb") as f:
        f.seek(0,2); end=f.tell(); f.seek(OFF[0]); news=f.read().decode("utf-8","replace"); OFF[0]=end
        f.seek(max(0,end-400000)); tail=f.read().decode("utf-8","replace")
    line=""
    for ln in tail.splitlines():
        if "strata serve: prompt" in ln: line=ln
    m=re.findall(r"([\d.]+) tok/s",line); acc=re.search(r"drafts accepted (\d+) of (\d+)",line)
    return {"wall":wall,"ptok":r.get("usage",{}).get("prompt_tokens"),"ctok":r.get("usage",{}).get("completion_tokens"),
            "pp":float(m[0]) if m else None,"tg":float(m[1]) if len(m)>1 else None,
            "acc":(int(acc.group(1)),int(acc.group(2))) if acc else None,"hash":h}
u=ask(FILL*20,1); per=u["ptok"]/20.0; p8=FILL*max(1,int((8000-40)/per))
ask(p8,8)   # warm the prefix
rows=[]
for i in range(N):
    r=ask(p8,512); rows.append(r)
    print("R%d tg=%s acc=%s ctok=%s hash=%s"%(i+1,r["tg"],r["acc"],r["ctok"],r["hash"]),flush=True)
tgs=sorted(x["tg"] for x in rows if x["tg"])
accs=[x["acc"][0]/x["acc"][1] for x in rows if x["acc"] and x["acc"][1]]
out={"tag":TAG,"ts":time.strftime("%F %T"),"n":N,"tg_med":round(statistics.median(tgs),2) if tgs else None,
     "tg_min":tgs[0] if tgs else None,"tg_max":tgs[-1] if tgs else None,
     "acc_med":round(statistics.median(accs),4) if accs else None,"ctok_set":sorted({x["ctok"] for x in rows})}
with open(os.path.join(os.environ.get("SB_OUT","."),"tg8k.json"),"a") as f: f.write(json.dumps(out)+"\n")
print("TG8",json.dumps(out),flush=True)

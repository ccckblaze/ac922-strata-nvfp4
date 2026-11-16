#!/usr/bin/env python3.11
"""cap-pressure probe: N conversations, cold build -> sequential continuations -> concurrent continuations.

Answers one question: when more long conversations are live than `--prompt-cache` can hold, how
deep does each continuation fall back (gap), and what does the chain cost in resident memory?
Reads only the engine's self-report lines and prints numbers plus a 12-hex digest of nothing but
its own counters; no model text is emitted.

env: SB_URL, SB_ENG, SB_PID (serve pidfile), NC, BASE_TOK, GAPS, SB_OUT.
"""
import json,os,re,time,threading,urllib.request
URL=os.environ.get("SB_URL","http://127.0.0.1:8092"); ENG=os.environ.get("SB_ENG","/data/champion/strata-nvfp4-dev-engine.log")
TAG=os.environ.get("SB_TAG","CTL"); MODEL=os.environ.get("SB_MODEL","qwen3.8-flash-next")
NC=int(os.environ.get("NC","4")); BASE_TOK=int(os.environ.get("BASE_TOK","30000"))
KEY=os.environ.get("STRATA_API_KEY",""); OFF=[os.path.getsize(ENG)]; LOCK=threading.Lock()
F=["Reimagine a lighthouse keeper logging nightly waves and distant ships; ",
   "Document the migration routes of arctic terns across eleven seasonal storms; ",
   "Catalogue the repair logs of a tram depot through four winters of steel and grease; ",
   "Transcribe the tide tables of a fishing village where every dock has a rumour; "]
def post(payload,timeout=1200):
    r=urllib.request.Request(URL+"/v1/chat/completions",data=json.dumps(payload).encode(),
        headers={"Content-Type":"application/json","Authorization":"Bearer "+KEY})
    with urllib.request.urlopen(r,timeout=timeout) as x: return json.loads(x.read())
POOL=[]   # every self-report line seen so far, shared: concurrent readers must not steal rows
def drain():
    with LOCK:
        with open(ENG,"rb") as f:
            f.seek(OFF[0]); d=f.read().decode("utf-8","replace"); OFF[0]=f.tell()
        POOL.extend(l for l in d.splitlines() if "strata serve: prompt" in l)
        return list(POOL)
PAT=re.compile(r"prompt (\d+) tokens = (\d+) reused \+ ([\d,]+)(?: of \d+)? read in (\d+) ms")
def rss():
    """serve RSS in GiB, so chain growth is priced, not just gap."""
    try:
        pid=open(os.environ.get("SB_PID","/tmp/strata-nvfp4-dev.pid")).read().strip()
        for l in open("/proc/%s/status"%pid):
            if l.startswith("VmRSS"): return round(int(l.split()[1])/1048576.0,2)
    except Exception: return None
    return None
def ask(text,label,out):
    t0=time.time(); u=post({"model":MODEL,"messages":[{"role":"user","content":text}],"temperature":0,"max_tokens":1})
    pt=u.get("usage",{}).get("prompt_tokens"); rec=None
    for _ in range(40):
        for l in drain():
            m=PAT.search(l)
            if m and int(m.group(1))==pt: rec=m
        if rec is not None: break
        time.sleep(0.25)
    row={"label":label,"ptok":pt,"reused":int(rec.group(2)) if rec else None,"read":int(rec.group(3).replace(",","")) if rec else None,
         "pms":int(rec.group(4)) if rec else None,"wall":round(time.time()-t0,2),"rss_gib":rss()}
    row["gap"]=(row["ptok"]-row["reused"]) if row["reused"] is not None else None
    out.append(row); print("%-14s ptok=%-7d reused=%-7s gap=%-7s pms=%-7s ms"%(label,pt,row["reused"],row["gap"],row["pms"]),flush=True)
def build(i):
    per=None
    u=[]; ask(F[i]*20,"calib%d"%i,u); per=u[-1]["ptok"]/20.0
    return F[i]*max(1,int((BASE_TOK-40)/per)),per
res=[]; bases=[]; pers=[]
for i in range(NC):
    t,p=build(i); bases.append(t); pers.append(p); res.append({"kind":"calib","ptok":None})
print("--- phase1: cold build %d conversations ---"%NC,flush=True)
for i,t in enumerate(bases): ask(t,"cold%d"%i,res)
print("--- phase2: sequential continuations (small fresh tail) ---",flush=True)
for i,t in enumerate(bases): ask(t+("Tail note %d: "%i)+F[i]*20,"seq%d"%i,res)
print("--- phase3: concurrent continuations ---",flush=True)
th=[]
for i,t in enumerate(bases):
    txt=t+("Round %d continuation: "%i)+F[(i+1)%NC]*30
    h=[]; th.append((threading.Thread(target=ask,args=(txt,"par%d"%i,res)),h))
for x,_ in th: x.start()
for x,_ in th: x.join()
gaps=sorted(r["gap"] for r in res if r.get("gap"))
pmss=sorted(r["pms"] for r in res if r.get("pms") and r.get("gap"))
def med(v): return v[len(v)//2] if v else None
rss=[r["rss_gib"] for r in res if r.get("rss_gib")]
json.dump({"tag":TAG,"ts":time.strftime("%F %T"),"rows":res},
          open(os.path.join(os.environ.get("SB_OUT","."),"cprobe-%s.json"%TAG),"w"))
print("SUMMARY %s: rows=%d gap med=%s p90=%s max=%s | pms med=%s | rss %s->%s GiB"%(
      TAG,len(gaps),med(gaps),gaps[int(len(gaps)*0.9)] if gaps else None,max(gaps) if gaps else None,
      med(pmss),rss[0] if rss else None,rss[-1] if rss else None),flush=True)
print("DONE %s"%TAG,flush=True)

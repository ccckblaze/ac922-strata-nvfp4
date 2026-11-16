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
MULTI=int(os.environ.get("MULTI","0")); NTURNS=int(os.environ.get("NTURNS","10"))
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
    """Engine RSS in GiB, so checkpoint growth is priced, not just gap.

    The lane pidfile names the serve *supervisor* (~0.2 GiB); the memory is in its strata child,
    so sum the tree."""
    try:
        root=int(open(os.environ.get("SB_PID","/tmp/strata-nvfp4-dev.pid")).read().strip())
    except Exception:
        return None
    kids={root:[]}; tot=0
    try:
        for d in os.listdir("/proc"):
            if not d.isdigit(): continue
            try:
                st=open("/proc/%s/stat"%d).read().rsplit(")",1)[1].split()
                kids.setdefault(int(st[1]),[]).append(int(d))
                tot+=int(open("/proc/%s/statm"%d).read().split()[1])*4096//1048576
                if int(d)!=root and int(st[1])!=root: tot-=int(open("/proc/%s/statm"%d).read().split()[1])*4096//1048576
            except Exception: pass
    except Exception:
        return None
    return round(tot/1024.0,2)
def ask(msgs,label,out):
    """`msgs` is a string (one user message) or a messages list (multi-turn shape); a list must
    reach the server as a list, otherwise the transcript silently collapses to its last turn."""
    if isinstance(msgs,str): msgs=[{"role":"user","content":msgs}]
    t0=time.time(); u=post({"model":MODEL,"messages":msgs,"temperature":0,"max_tokens":1})
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
def turns(text,i):
    """the same content as a multi-turn transcript (the shape real agents send, and the shape that
    gets whole-prefix conversation-cache relays -- see the 2026-11-16 shape A/B)."""
    k=max(1,len(text)//NTURNS); out=[{"role":"system","content":"You keep a running log."}]
    for t in range(NTURNS):
        seg=text[t*k:(t+1)*k] or text[:k]
        out.append({"role":"user","content":seg if t%2==0 else "Turn %d: "%t+seg})
        out.append({"role":"assistant","content":"Noted %d."%t})
    return out
def payload(text,i):
    return turns(text,i) if MULTI else [{"role":"user","content":text}]
def unit(i):
    return F[i%len(F)]
def build(i):
    """each conversation must be its OWN prefix, or NC > len(F) conversations reuse each other's
    chains and the cap experiment measures nothing."""
    u=[]; ask(unit(i)*20,"calib%d"%i,u); per=u[-1]["ptok"]/20.0
    tag="Conversation %d log: "%i
    return tag+unit(i)*max(1,int((BASE_TOK-40-len(tag))/per)),per
res=[]; bases=[]; pers=[]
for i in range(NC):
    t,p=build(i); bases.append(t); pers.append(p); res.append({"kind":"calib","ptok":None})
print("--- phase1: cold build %d conversations ---"%NC,flush=True)
for i,t in enumerate(bases): ask(payload(t,i),"cold%d"%i,res)
print("--- phase2: sequential continuations (small fresh tail) ---",flush=True)
for i,t in enumerate(bases): ask(payload(t,i)+[{"role":"user","content":"Tail note %d: "%i+unit(i)*30}],"seq%d"%i,res) if MULTI else ask(t+("Tail note %d: "%i)+unit(i)*30,"seq%d"%i,res)
print("--- phase3: concurrent continuations ---",flush=True)
th=[]
for i,t in enumerate(bases):
    txt=(payload(t,i)+[{"role":"assistant","content":"Ack %d."%i},{"role":"user","content":"Round %d: "%i+unit(i+1)*30}]) if MULTI else t+("Round %d continuation: "%i)+unit(i+1)*30
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

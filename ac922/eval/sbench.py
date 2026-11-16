import json,re,sys,time,hashlib,urllib.request,urllib.error,os
URL=os.environ.get("SB_URL","http://127.0.0.1:8090"); ENG=os.environ.get("SB_ENG","/data/champion/strata-engine.log")
FILL="Reimagine a lighthouse keeper logging nightly waves and distant ships; "
KEY=os.environ.get("STRATA_API_KEY","")
def post(path,payload,timeout=7200):
    """Authenticated POST with a loud failure.

    The lane enforces STRATA_API_KEY from 2026-11-16 on.  Before this, an unauthenticated call raised
    401 inside every gate fixture, the wrapper swallowed it and the gate scored zero rows as a result:
    a refused request must abort the gate, never silently become a verdict.
    """
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
def ask(text,max_new=64):
    t0=time.time()
    r=post("/v1/chat/completions",{"model":"qwen3.8-flash-next","messages":[{"role":"user","content":text}],"temperature":0,"max_tokens":max_new})
    msg=r["choices"][0]["message"]
    h=hashlib.sha256(json.dumps(msg,sort_keys=True).encode()).hexdigest()[:12]
    line=""
    with open(ENG,"rb") as f:
        f.seek(max(0,f.tell()-0)); data=f.read()[-200000:]
    for ln in data.decode("utf-8","replace").splitlines():
        if "strata serve: prompt" in ln: line=ln
    m=re.findall(r"([\d.]+) tok/s", line)
    pp=m[0] if m else "NA"; tg=m[1] if len(m)>1 else "NA"
    acc=re.search(r"drafts accepted (\d+) of (\d+)",line); 
    return {"wall":round(time.time()-t0,1),"ptok":r.get("usage",{}).get("prompt_tokens"),"ctok":r.get("usage",{}).get("completion_tokens"),"pp":pp,"tg":tg,"acc":acc.groups() if acc else None,"hash":h}
def build(target):
    n0=None
    for _ in range(2):
        u=post("/v1/chat/completions",{"model":"qwen3.8-flash-next","messages":[{"role":"user","content":FILL*20}],"temperature":0,"max_tokens":1})
        per=u["usage"]["prompt_tokens"]/20.0
    k=max(1,int(target/per))
    text=FILL*k
    return text,k
def main():
    cmd=sys.argv[1] if len(sys.argv)>1 else "long"
    if cmd=="det2":
        # early-close sentinel: the tail-skip ticket's known failure mode is the reasoning chain
        # ending sooner, which the 32-token det case cannot see by construction.  300 tokens, and
        # report what stopped each one.  Numbers and hashes only.
        text,n=build(10000); text="QFIX20261113 "+text; print("det2 base tokens",n)
        for i in range(3):
            r=post("/v1/chat/completions",{"model":"qwen3.8-flash-next","messages":[{"role":"user","content":text}],"temperature":0,"max_tokens":300})
            ch=r["choices"][0]; msg=ch["message"]; u=r.get("usage",{})
            c=msg.get("content") or ""
            h=hashlib.sha256(json.dumps(msg,sort_keys=True).encode()).hexdigest()[:12]
            print(json.dumps({"stop":ch.get("finish_reason"),"ctok":u.get("completion_tokens"),"chars":len(c),"hash":h}))
    if cmd=="det":
        text,n=build(10000); text="QFIX20261113 "+text; print("det base tokens",n)
        for i in range(3): print(json.dumps(ask(text,32)))
    elif cmd=="long":
        for tgt in [8000,64000,131000,196000,249000]:
            text,n=build(tgt); res=ask(text,64); print("TARGET",tgt,json.dumps(res),flush=True)
    elif cmd=="conc":
        tgt=int(sys.argv[2]); n=int(sys.argv[3]) if len(sys.argv)>3 else 4
        base,_=build(tgt)
        texts=[("Stream %d unique marker. "%i)+base for i in range(n)]
        import threading
        out=[None]*n
        def w(i): out[i]=ask(texts[i],64)
        ts=[threading.Thread(target=w,args=(i,)) for i in range(n)]
        t0=time.time()
        for t in ts: t.start()
        for t in ts: t.join()
        print("wall=%.1f"%(time.time()-t0))
        for o in out: print(json.dumps(o))
        print("hashes", set(o["hash"] for o in out))
    elif cmd=="probe":
        print(json.dumps(ask("Say hello.",8)))
if __name__=="__main__": main()

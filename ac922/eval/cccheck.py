import json,time,hashlib,urllib.request,urllib.error,os,sys
URL=os.environ.get("SB_URL","http://127.0.0.1:8091"); FILL="Reimagine a lighthouse keeper logging nightly waves and distant ships; "
KEY=os.environ.get("STRATA_API_KEY","")
def post(payload,timeout=7200):
    d=json.dumps(payload).encode()
    hd={"Content-Type":"application/json"}
    if KEY: hd["Authorization"]="Bearer "+KEY
    r=urllib.request.Request(URL+"/v1/chat/completions",data=d,headers=hd)
    try:
        with urllib.request.urlopen(r,timeout=timeout) as x: return json.loads(x.read())
    except urllib.error.HTTPError as e:
        if e.code in (401,403):
            raise SystemExit("GATE-ERROR auth-%d: export STRATA_API_KEY before running the gate"%e.code)
        raise
def ask(text,max_new):
    t0=time.time(); r=post({"model":"qwen3.8-flash-next","messages":[{"role":"user","content":text}],"temperature":0,"max_tokens":max_new})
    msg=r["choices"][0]["message"]
    return hashlib.sha256(json.dumps(msg,sort_keys=True).encode()).hexdigest()[:12], r.get("usage",{}).get("prompt_tokens"), round(time.time()-t0,2)
def build(target):
    u=post({"model":"qwen3.8-flash-next","messages":[{"role":"user","content":FILL*20}],"temperature":0,"max_tokens":1})
    per=u["usage"]["prompt_tokens"]/20.0
    return "CCHK20261114 "+FILL*max(1,int(target/per))
tag=sys.argv[1] if len(sys.argv)>1 else "A"
L=[8500,64000,131000]
out=[]
for tgt in L:
    t=build(tgt)
    cold=ask(t,32)
    cache=[ask(t,32) for _ in range(3)]
    same=cold[0]==cache[0][0]==cache[1][0]==cache[2][0]
    print(json.dumps({"run":tag,"tgt":tgt,"cold":cold[0],"cold_ptok":cold[1],"cold_s":cold[2],
                      "cache":[(c[0],c[2]) for c in cache],"cold_eq_cache":same}),flush=True)
    out.append(same)
print("CC-%s %s %s"%(tag,"ALL-EQ" if all(out) else "DIVERGE",sum(out)),flush=True)

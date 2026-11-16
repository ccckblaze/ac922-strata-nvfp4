import json,sys,time,hashlib,urllib.request,os
URL=os.environ.get("SB_URL","http://127.0.0.1:8091"); FILL="Reimagine a lighthouse keeper logging nightly waves and distant ships; "
KEY=os.environ.get("STRATA_API_KEY","")
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
    r=post("/v1/chat/completions",{"model":"qwen3.8-flash-next","messages":[{"role":"user","content":text}],"temperature":0,"max_tokens":max_new})
    msg=r["choices"][0]["message"]
    return hashlib.sha256(json.dumps(msg,sort_keys=True).encode()).hexdigest()[:12], r.get("usage",{}).get("prompt_tokens")
def build(target):
    u=post("/v1/chat/completions",{"model":"qwen3.8-flash-next","messages":[{"role":"user","content":FILL*20}],"temperature":0,"max_tokens":1})
    per=u["usage"]["prompt_tokens"]/20.0
    return "QPRB20261113 "+FILL*max(1,int(target/per))
tag=sys.argv[1] if len(sys.argv)>1 else "A"
for tgt in (64000,131000):
    t=build(tgt)
    for L in (8,32,128,512):
        h,pt=ask(t,L)
        print(json.dumps({"run":tag,"tgt":tgt,"L":L,"h":h,"ptok":pt}),flush=True)
print("PROBE-DONE "+tag)

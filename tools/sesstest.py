#!/usr/bin/env python3
# Multi-turn same-session long-context test against the MTPLX server.
# Usage: python3 tools/sesstest.py OUT_DIR "$(cat ~/.mtplx/api-key)" "$(uv tool dir)/mtplx/lib/python3.12/site-packages/mtplx" SESSION_ID
# (run tools/make-memtest-prompt.py OUT_DIR first). Prints per-turn memory/cache stats from the request log.
import sys,json,time,urllib.request,glob,os
S,K,site,sid=sys.argv[1:5]
LOG=os.path.expanduser('~/.mtplx/logs/request-log-8000.jsonl')
msgs=json.load(open(S+'/memtest-msgs.json'))
def stats():
    r=json.loads(open(LOG).read().strip().splitlines()[-1]); g=lambda k:(r.get(k) or 0)/2**30
    return f"cached={r.get('cached_tokens')} src={r.get('cache_source')} miss={r.get('cache_miss_reason')} ttft={round(r.get('ttft_s') or 0,1)} dec={round(r.get('decode_tok_s') or 0,1)} active={g('active_memory_bytes'):.1f} peak={g('peak_memory_bytes'):.1f} err={r.get('error_kind')} memo_rebuilds={r.get('paged_kv_quant_dequant_memo_rebuilds')} kvq={r.get('paged_kv_quant_mode')} restore={r.get('session_restore_mode')} liveref={r.get('request_session_keep_live_ref')} route={r.get('prefill_route')}"
def call(msgs,tag):
    body={"model":"qwen3.8-27b-abliterated-grant","max_tokens":60,"chat_template_kwargs":{"enable_thinking":False},"messages":msgs}
    req=urllib.request.Request("http://192.168.42.1:8000/v1/chat/completions",data=json.dumps(body).encode(),
        headers={"Authorization":"Bearer "+K,"content-type":"application/json","x-mtplx-client":"opencode","x-mtplx-session-id":sid})
    t=time.time()
    try:
        d=json.load(urllib.request.urlopen(req,timeout=2400)); out=d['choices'][0]['message']['content']
        print(tag,"OK",round(time.time()-t,1),"s prompt",d['usage']['prompt_tokens'],"|",stats(),flush=True); return out
    except urllib.error.HTTPError as e:
        time.sleep(1); print(tag,"HTTP",e.code,"|",stats(),flush=True)
extra=open(sorted(glob.glob(site+'/server/*.py'),key=os.path.getsize)[-3],errors='ignore').read()[:30000]
for tag,q in [("t1",None),("t2","Now name one other file above that deals with sessions, in one sentence."),
              ("t3","Here is one more file:\n"+extra+"\n\nOne sentence: what does it do?"),("t4","Thanks. One word: yes or no, is it Python?"),
              ("t5","And one more file:\n"+open(sorted(glob.glob(site+'/server/*.py'),key=os.path.getsize)[-4],errors='ignore').read()[:30000]+"\n\nOne sentence: what does it do?")]:
    if q: msgs.append({"role":"user","content":q})
    a=call(msgs,tag)
    if a is None: break
    msgs.append({"role":"assistant","content":a})

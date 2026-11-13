# usage: python3 - <label> <n> <concurrency> [priority]   (공유 vLLM 파드 안에서 localhost:8000 으로 부하를 건다)
import json, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
label, n, c = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
prio = int(sys.argv[4]) if len(sys.argv) > 4 else None
url = "http://127.0.0.1:8000/v1/chat/completions"
def one(_):
    d = {"model":"Qwen/Qwen2.5-3B-Instruct","messages":[{"role":"user","content":"Explain what a GPU is in detail."}],"max_tokens":128,"ignore_eos":True,"temperature":0}
    if prio is not None: d["priority"] = prio
    t = time.time()
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(url, json.dumps(d).encode(), {"Content-Type":"application/json"}), timeout=600))
    except urllib.error.HTTPError as e:
        return None, e.read().decode()[:200]
    return time.time() - t, r["usage"]["completion_tokens"]
t0 = time.time()
with ThreadPoolExecutor(c) as ex: res = list(ex.map(one, range(n)))
bad = [x for x in res if x[0] is None]
if bad: print(label, "ERROR", bad[0][1]); sys.exit(1)
w = time.time() - t0; lat = sorted(x[0] for x in res)
print(label, json.dumps({"n": n, "c": c, "prio": prio, "wall_s": round(w, 1), "tok_s": round(sum(x[1] for x in res) / w), "p50": round(lat[len(lat)//2], 2), "p95": round(lat[int(len(lat)*.95)], 2)}))

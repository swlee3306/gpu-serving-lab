# usage: python3 - <label> <n> <concurrency>   (vLLM 파드 안에서 localhost:8000 으로 부하를 건다)
import json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
label, n, c = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
url = "http://127.0.0.1:8000/v1/chat/completions"
def one(_):
    body = json.dumps({"model":"Qwen/Qwen2.5-3B-Instruct","messages":[{"role":"user","content":"Explain what a GPU is in detail."}],"max_tokens":128,"ignore_eos":True,"temperature":0}).encode()
    t = time.time(); r = json.load(urllib.request.urlopen(urllib.request.Request(url, body, {"Content-Type":"application/json"}), timeout=600))
    return time.time() - t, r["usage"]["completion_tokens"]
t0 = time.time()
with ThreadPoolExecutor(c) as ex: res = list(ex.map(one, range(n)))
w = time.time() - t0; lat = sorted(x[0] for x in res)
print(label, json.dumps({"n": n, "c": c, "wall_s": round(w, 1), "tok_s": round(sum(x[1] for x in res) / w), "p50": round(lat[len(lat)//2], 2), "p95": round(lat[int(len(lat)*.95)], 2)}))

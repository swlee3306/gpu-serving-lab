# usage: python3 swap.py <from_port> <to_port> : from 을 재우고(level 1) to 를 깨운 뒤 첫 요청까지 걸린 시간을 잰다.
import json, sys, time, urllib.request
def post(port, path):
    t = time.time(); urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}{path}", b"", method="POST"), timeout=300).read(); return time.time() - t
def chat(port):
    d = {"model":"Qwen/Qwen2.5-3B-Instruct","messages":[{"role":"user","content":"Say hi."}],"max_tokens":16,"temperature":0}
    t = time.time(); urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", json.dumps(d).encode(), {"Content-Type":"application/json"}), timeout=300).read(); return time.time() - t
a, b = sys.argv[1], sys.argv[2]
t0 = time.time()
ts = post(a, "/sleep?level=1"); tw = post(b, "/wake_up"); tc = chat(b)
print(json.dumps({"sleep_s": round(ts, 2), "wake_s": round(tw, 2), "first_req_s": round(tc, 2), "switch_total_s": round(time.time() - t0, 2)}))

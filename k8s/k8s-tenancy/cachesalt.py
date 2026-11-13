# 공유 vLLM 안에서 테넌트 A 가 보낸 긴 프롬프트의 프리픽스 캐시를 테넌트 B 가 지연 시간으로 알아챌 수 있는지 잰다.
# 각 시도마다 새 프롬프트(약 1,200단어(약 4천 토큰))를 쓰고 max_tokens=1 로 요청해서 지연 ≈ TTFT 가 되게 한다.
import json, random, statistics, sys, time, urllib.request
url = "http://127.0.0.1:8000/v1/chat/completions"
rng = random.Random(time.time_ns())
words = [''.join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(3, 9))) for _ in range(2000)]
def prompt(): return "Summarize: " + " ".join(rng.choice(words) for _ in range(1200))
def ask(p, salt=None):
    d = {"model": "Qwen/Qwen2.5-3B-Instruct", "messages": [{"role": "user", "content": p}], "max_tokens": 1, "temperature": 0}
    if salt: d["cache_salt"] = salt
    t = time.time(); r = json.load(urllib.request.urlopen(urllib.request.Request(url, json.dumps(d).encode(), {"Content-Type": "application/json"}), timeout=120))
    return time.time() - t, r["usage"]["prompt_tokens"]
N = 5; cold, nosalt_b, salt_a, salt_b = [], [], [], []
toks = 0
for _ in range(N):
    p = prompt(); t, toks = ask(p); cold.append(t)            # A, salt 없음: 캐시에 없는 새 프롬프트
    t, _ = ask(p); nosalt_b.append(t)                          # B, salt 없음, 같은 프롬프트
    p = prompt(); t, _ = ask(p, "tenant-a"); salt_a.append(t)  # A, salt=tenant-a
    t, _ = ask(p, "tenant-b"); salt_b.append(t)                # B, salt=tenant-b, 같은 프롬프트
m = lambda x: round(statistics.median(x) * 1000)
print("프롬프트 토큰 약", toks, "| 시도", N)
print("A 첫 요청(캐시 없음)                 p50 %d ms" % m(cold))
print("B 같은 프롬프트, salt 없음            p50 %d ms" % m(nosalt_b))
print("A 첫 요청, salt=tenant-a             p50 %d ms" % m(salt_a))
print("B 같은 프롬프트, salt=tenant-b        p50 %d ms" % m(salt_b))
print("raw(ms) cold", [round(x*1000) for x in cold], "nosalt_b", [round(x*1000) for x in nosalt_b], "salt_b", [round(x*1000) for x in salt_b])

#!/usr/bin/env python3
"""Open-loop, mixed-size load generator for an OpenAI-compatible server (standard library only).

Unlike bench.py (c requests always in flight, identical prompts), this one imitates real traffic:
  * requests ARRIVE at random times at a target rate (Poisson arrivals), whether or not the server keeps up
  * every prompt is different (random document tag + filler of random length) -> prefix cache cannot share it
  * answer length varies (32..512 tokens, forced with ignore_eos so the size is known)
"""
import argparse, http.client, json, math, random, threading, time

TOPICS = [
    "쿠버네티스를 한 문단으로 설명해줘.", "파이썬 리스트와 튜플의 차이는?", "도커 이미지와 컨테이너의 차이를 알려줘.",
    "REST API와 GraphQL을 비교해줘.", "TCP와 UDP의 차이를 설명해줘.", "데이터베이스 인덱스는 왜 필요해?",
    "블루그린 배포와 카나리 배포를 비교해줘.", "SSH 키 인증이 비밀번호보다 안전한 이유는?", "로드 밸런서의 역할을 설명해줘.",
    "캐시 무효화가 어려운 이유는?", "프로세스와 스레드의 차이는?", "GPU가 CPU보다 행렬 계산에 빠른 이유는?",
    "메시지 큐를 쓰는 이유를 알려줘.", "멱등성이 왜 중요한지 예를 들어 설명해줘.", "HTTPS 인증서 발급 과정을 요약해줘.",
    "리눅스 파일 권한 755의 의미는?", "CI/CD 파이프라인의 단계를 설명해줘.", "분산 시스템에서 일관성과 가용성의 충돌을 설명해줘.",
    "Prometheus와 Grafana의 역할 차이는?", "로그, 메트릭, 트레이스의 차이를 알려줘.", "가상머신과 컨테이너의 차이는?",
    "DNS가 도메인을 IP로 바꾸는 과정을 설명해줘.", "NAT가 필요한 이유는?", "백업과 복제의 차이는?",
    "장애 복구에서 RTO와 RPO가 뭐야?", "서비스 메시가 해결하는 문제는?", "토큰 기반 인증(JWT)의 장단점은?",
    "스케줄러가 파드를 노드에 배치하는 기준은?", "쿼터와 제한(limit)의 차이를 설명해줘.", "롤백이 가능한 배포 설계 방법은?",
]
WORDS = ["alpha", "bravo", "cobalt", "delta", "ember", "falcon", "garnet", "harbor", "indigo", "juniper", "kepler", "lumen"]
OUT_LENS = [(32, 0.20), (64, 0.30), (128, 0.30), (256, 0.15), (512, 0.05)]      # mean about 128 tokens


def make_request(rng):
    q = rng.choice(TOPICS)
    n_words = max(0, min(700, int(rng.lognormvariate(math.log(80), 0.9))))         # median 80 words, long tail
    filler = " ".join(rng.choice(WORDS) + str(rng.randint(0, 9999)) for _ in range(n_words))
    prompt = f"[문서 {rng.randint(0, 10**9)}] {filler}\n\n위 문서는 참고용이야. 질문: {q}" if n_words else q
    r, acc, mt = rng.random(), 0.0, 128
    for length, w in OUT_LENS:
        acc += w
        if r <= acc:
            mt = length; break
    return prompt, mt


def system_text(words):
    rng = random.Random(12345)
    return "너는 사내 기술 지원 챗봇이다. 아래 규칙을 항상 지켜라. " + " ".join(rng.choice(WORDS) + str(rng.randint(0, 99)) for _ in range(words)) if words else ""


def one_request(args, prompt, max_tokens):
    msgs = ([{"role": "system", "content": args.sys_text}] if args.sys_text else []) + [{"role": "user", "content": prompt}]
    body = json.dumps({"model": args.model, "messages": msgs, "max_tokens": max_tokens,
                       "temperature": 0, "chat_template_kwargs": ({"enable_thinking": False} if args.no_think else {}), "stream": True, "stream_options": {"include_usage": True}, "ignore_eos": True})
    conn = http.client.HTTPConnection(args.host, args.port, timeout=600)
    t0 = time.perf_counter()
    conn.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    if resp.status != 200:
        raise RuntimeError(f"HTTP {resp.status}: {resp.read()[:160]!r}")
    ttft, comp, prompt_tokens = None, 0, 0
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        ch = chunk.get("choices") or []
        if ttft is None and ch and ch[0].get("delta", {}).get("content"):
            ttft = time.perf_counter() - t0
        if chunk.get("usage"):
            comp, prompt_tokens = chunk["usage"]["completion_tokens"], chunk["usage"]["prompt_tokens"]
    total = time.perf_counter() - t0
    conn.close()
    return ttft, total, comp, prompt_tokens


def pct(v, p):
    s = sorted(v)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def run_rate(args, rate):
    rng = random.Random(args.seed + int(rate * 1000))
    results, errors, lock = [], [], threading.Lock()
    state = {"inflight": 0, "peak": 0, "dropped": 0, "sent": 0}
    threads = []

    def worker(prompt, mt):
        try:
            r = one_request(args, prompt, mt)
            with lock:
                results.append(r)
        except Exception as e:
            with lock:
                errors.append(str(e)[:100])
        finally:
            with lock:
                state["inflight"] -= 1

    t_start = time.perf_counter(); t_next = t_start
    while time.perf_counter() - t_start < args.duration:
        now = time.perf_counter()
        if now < t_next:
            time.sleep(min(0.002, t_next - now)); continue
        t_next += rng.expovariate(rate)                          # random gap -> Poisson arrivals
        with lock:
            if state["inflight"] >= args.max_inflight:
                state["dropped"] += 1; continue
            state["inflight"] += 1; state["peak"] = max(state["peak"], state["inflight"]); state["sent"] += 1
        p, mt = make_request(rng)
        th = threading.Thread(target=worker, args=(p, mt)); th.start(); threads.append(th)
    for th in threads:
        th.join()
    wall = time.perf_counter() - t_start
    ok = [r for r in results if r[0] is not None]
    if not ok:
        return {"rate": rate, "sent": state["sent"], "ok": 0, "errors": errors[:2]}
    ttft = [r[0] for r in ok]; lat = [r[1] for r in ok]; tok = sum(r[2] for r in ok)
    tps = [r[2] / r[1] for r in ok if r[1] > 0]; ptok = [r[3] for r in ok]
    row = {"rate": rate, "sent": state["sent"], "ok": len(ok), "err": len(errors), "dropped": state["dropped"],
           "peak_inflight": state["peak"], "wall_s": round(wall, 1), "req_per_s": round(len(ok) / wall, 1),
           "throughput_tps": round(tok / wall, 0), "per_request_tps_p50": round(pct(tps, 50), 1),
           "ttft_p50_ms": round(pct(ttft, 50) * 1000), "ttft_p95_ms": round(pct(ttft, 95) * 1000), "ttft_p99_ms": round(pct(ttft, 99) * 1000),
           "latency_p50_s": round(pct(lat, 50), 2), "latency_p95_s": round(pct(lat, 95), 2),
           "prompt_tokens_p50": pct(ptok, 50), "prompt_tokens_p95": pct(ptok, 95), "completion_mean": round(tok / len(ok))}
    row["slo_ok"] = bool(row["ttft_p95_ms"] <= args.slo_ttft_ms and row["per_request_tps_p50"] >= args.slo_tps and not errors and not state["dropped"])
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--model", default="Qwen/Qwen2.5-3B-Instruct")
    ap.add_argument("--rates", default="10,20,40,80", help="target arrival rates in requests/second")
    ap.add_argument("--duration", type=float, default=30, help="seconds of arrivals per rate")
    ap.add_argument("--max-inflight", type=int, default=600)
    ap.add_argument("--slo-ttft-ms", type=int, default=300); ap.add_argument("--slo-tps", type=float, default=30)
    ap.add_argument("--stop-after-fail", type=int, default=0, help="stop the rate sweep after this many SLO failures (0 = never)")
    ap.add_argument("--system-words", type=int, default=0, help="shared fixed system prompt of N filler words (same for every request -> prefix cache can reuse it)")
    ap.add_argument("--no-think", action="store_true", help="send chat_template_kwargs enable_thinking=false")
    ap.add_argument("--seed", type=int, default=7); ap.add_argument("--out", default="")
    args = ap.parse_args()
    args.sys_text = system_text(args.system_words)
    one_request(args, "안녕", 8)
    rows, fails = [], 0
    hdr = f"{'도착/s':>6} {'완료':>5} {'오류':>4} {'버림':>4} {'처리량tok/s':>11} {'1인tok/s':>8} {'TTFT p50':>9} {'TTFT p95':>9} {'TTFT p99':>9} {'지연p95':>8} {'최대동시':>7} {'프롬프트p50/p95':>16} SLO"
    print(hdr); print("-" * len(hdr))
    for rate in [float(x) for x in args.rates.split(",")]:
        r = run_rate(args, rate); rows.append(r)
        if r["ok"] == 0:
            print(f"{rate:>6} 실패 {r.get('errors')}"); continue
        print(f"{rate:>6g} {r['ok']:>5} {r['err']:>4} {r['dropped']:>4} {r['throughput_tps']:>11.0f} {r['per_request_tps_p50']:>8} "
              f"{r['ttft_p50_ms']:>7}ms {r['ttft_p95_ms']:>7}ms {r['ttft_p99_ms']:>7}ms {r['latency_p95_s']:>7}s {r['peak_inflight']:>7} "
              f"{str(r['prompt_tokens_p50'])+'/'+str(r['prompt_tokens_p95']):>16} {'통과' if r['slo_ok'] else '실패'}", flush=True)
        fails += 0 if r["slo_ok"] else 1
        if args.stop_after_fail and fails >= args.stop_after_fail:
            break
    if args.out:
        json.dump({"model": args.model, "slo": {"ttft_p95_ms": args.slo_ttft_ms, "tps": args.slo_tps}, "rows": rows},
                  open(args.out, "w"), ensure_ascii=False, indent=1)
        print("saved:", args.out)


if __name__ == "__main__":
    main()

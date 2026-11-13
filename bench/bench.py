#!/usr/bin/env python3
"""Tiny load generator for an OpenAI-compatible server (python standard library only).

For each concurrency level it keeps `c` requests in flight until `n` requests are done, and measures
  TTFT        time from sending the request to the first generated text
  latency     time from sending the request to the last token
  throughput  total generated tokens / wall-clock time (what the whole server delivers)
  per-request tokens/s  what ONE user experiences while the others are running
"""
import argparse, http.client, json, math, queue, threading, time

PROMPT = "쿠버네티스의 핵심 개념을 자세히 설명해줘."


def one_request(host, port, model, max_tokens):
    body = json.dumps({
        "model": model, "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": max_tokens, "temperature": 0,
        "stream": True, "stream_options": {"include_usage": True},
        "ignore_eos": True,            # always generate exactly max_tokens so every request has the same size
    })
    conn = http.client.HTTPConnection(host, port, timeout=600)
    t0 = time.perf_counter()
    conn.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    if resp.status != 200:
        raise RuntimeError(f"HTTP {resp.status}: {resp.read()[:200]!r}")
    ttft, tokens = None, 0
    for raw in resp:                                   # server-sent events: one "data: {...}" line per chunk
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        choices = chunk.get("choices") or []
        if ttft is None and choices and choices[0].get("delta", {}).get("content"):
            ttft = time.perf_counter() - t0             # first piece of text arrived
        if chunk.get("usage"):
            tokens = chunk["usage"]["completion_tokens"]
    total = time.perf_counter() - t0
    conn.close()
    return ttft, total, tokens


def pct(values, p):
    s = sorted(values)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def run_level(args, c, n):
    jobs = queue.Queue()
    for i in range(n):
        jobs.put(i)
    results, errors, lock = [], [], threading.Lock()

    def worker():
        while True:
            try:
                jobs.get_nowait()
            except queue.Empty:
                return
            try:
                r = one_request(args.host, args.port, args.model, args.max_tokens)
                with lock:
                    results.append(r)
            except Exception as e:                      # keep going, count the failure
                with lock:
                    errors.append(str(e)[:120])

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker) for _ in range(c)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    wall = time.perf_counter() - t0
    ok = [r for r in results if r[0] is not None]
    if not ok:
        return {"c": c, "n": n, "ok": 0, "errors": errors[:3]}
    ttft = [r[0] for r in ok]; lat = [r[1] for r in ok]; tok = sum(r[2] for r in ok)
    per_req = [r[2] / r[1] for r in ok if r[1] > 0]
    return {"c": c, "n": n, "ok": len(ok), "err": len(errors), "wall_s": round(wall, 2), "tokens": tok,
            "throughput_tps": round(tok / wall, 1),
            "per_request_tps_p50": round(pct(per_req, 50), 1),
            "ttft_p50_ms": round(pct(ttft, 50) * 1000), "ttft_p95_ms": round(pct(ttft, 95) * 1000),
            "latency_p50_s": round(pct(lat, 50), 2), "latency_p95_s": round(pct(lat, 95), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--model", default="Qwen/Qwen2.5-3B-Instruct")
    ap.add_argument("--levels", default="1,4,16,64", help="comma separated concurrency levels")
    ap.add_argument("--max-tokens", type=int, default=128)
    ap.add_argument("--per-level", type=int, default=4, help="requests per level = max(8, this x concurrency)")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    print(f"warm-up ...", flush=True)
    one_request(args.host, args.port, args.model, 16)
    rows = []
    hdr = f"{'동시':>4} {'성공':>4} {'처리량 tok/s':>12} {'1인 tok/s':>9} {'TTFT p50':>9} {'TTFT p95':>9} {'지연 p50':>8} {'지연 p95':>8}"
    print(hdr); print("-" * len(hdr))
    for c in [int(x) for x in args.levels.split(",")]:
        r = run_level(args, c, max(8, args.per_level * c)); rows.append(r)
        if r["ok"] == 0:
            print(f"{c:>4}  실패: {r['errors']}"); continue
        print(f"{r['c']:>4} {r['ok']:>4} {r['throughput_tps']:>12} {r['per_request_tps_p50']:>9} "
              f"{r['ttft_p50_ms']:>7}ms {r['ttft_p95_ms']:>7}ms {r['latency_p50_s']:>7}s {r['latency_p95_s']:>7}s", flush=True)
    if args.out:
        json.dump({"model": args.model, "max_tokens": args.max_tokens, "rows": rows}, open(args.out, "w"), ensure_ascii=False, indent=1)
        print("saved:", args.out)


if __name__ == "__main__":
    main()

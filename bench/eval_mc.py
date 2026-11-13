#!/usr/bin/env python3
"""Multiple-choice accuracy on evalset.json (100 KMMLU Computer-Science + 100 MMLU machine_learning), temperature 0.
Prints accuracy per language and writes the per-question answers so two runs can be diffed (control)."""
import argparse, http.client, json, re, threading
from concurrent.futures import ThreadPoolExecutor

def ask(args, q):
    body = json.dumps({"model": args.model, "temperature": 0, "max_tokens": 8,
        "chat_template_kwargs": ({"enable_thinking": False} if args.no_think else {}), "messages": [{"role": "user", "content": "다음 객관식 문제의 정답 선택지 알파벳(A, B, C, D) 하나만 답하세요.\n\n" + q}]})
    c = http.client.HTTPConnection(args.host, args.port, timeout=300)
    c.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
    r = c.getresponse(); d = json.loads(r.read()); c.close()
    t = d["choices"][0]["message"]["content"].strip()
    m = re.match(r"^\W*([A-D])\b", t) or re.search(r"\b([A-D])\b", t)
    return m.group(1) if m else "?"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--model", required=True); ap.add_argument("--set", default="/data/lab/bench/evalset.json")
    ap.add_argument("--no-think", action="store_true", help="send chat_template_kwargs enable_thinking=false (Qwen3.5 thinks by default)")
    ap.add_argument("--out", required=True); ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    items = json.load(open(a.set))
    with ThreadPoolExecutor(a.workers) as ex:
        preds = list(ex.map(lambda it: ask(a, it["q"]), items))
    res = {"model": a.model, "preds": {it["id"]: p for it, p in zip(items, preds)}}
    for lang in ("ko", "en"):
        sub = [(it, p) for it, p in zip(items, preds) if it["lang"] == lang]
        res[lang] = round(sum(p == it["a"] for it, p in sub) / len(sub) * 100, 1)
    res["all"] = round(sum(p == it["a"] for it, p in zip(items, preds)) / len(items) * 100, 1)
    res["unparsed"] = sum(p == "?" for p in preds)
    json.dump(res, open(a.out, "w"), ensure_ascii=False)
    print(f"정확도 ko={res['ko']}% en={res['en']}% 전체={res['all']}% (파싱실패 {res['unparsed']})")

if __name__ == "__main__":
    main()

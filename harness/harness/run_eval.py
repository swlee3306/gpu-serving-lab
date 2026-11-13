"""사용: python harness/run_eval.py --base-url http://127.0.0.1:8000 --model NAME --label LABEL [--repeat 2] [--t4-samples 3]"""
import argparse, json, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import prompts, scoring, scenario
from concurrent.futures import ThreadPoolExecutor
from llm import chat, chat_cli, extract_json

ROOT = pathlib.Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("--base-url", default="http://127.0.0.1:8000"); ap.add_argument("--model", default=""); ap.add_argument("--label", required=True)
ap.add_argument("--repeat", type=int, default=2); ap.add_argument("--t4-samples", type=int, default=3); ap.add_argument("--think", action="store_true"); ap.add_argument("--cli", choices=["claude", "codex"]); ap.add_argument("--workers", type=int, default=1)
a = ap.parse_args()
cases = json.load(open(ROOT / "data" / "cases.json", encoding="utf-8"))
res = {"label": a.label, "model": a.model, "t1": [], "t2": [], "t4": []}
t0 = time.time()
def call(p, temp, mx):
    return chat_cli(a.cli, a.model, p) if a.cli else chat(a.base_url, a.model, p, temp, mx, a.think)
def job(kind, c, rep):
    if kind == "t1":
        out, dt, _ = call(prompts.t1_prompt(c), 0.0, 400); return kind, {"id": c["id"], "rep": rep, "out": out, "sec": dt, **scoring.score_t1(c, out)}
    if kind == "t2":
        out, dt, _ = call(prompts.t2_prompt(c), 0.0, 700); return kind, {"id": c["id"], "rep": rep, "out": out, "sec": dt, **scoring.score_t2(c, out)}
    out, dt, _ = call(prompts.t4_prompt(), 0.7, 3500)
    return kind, {"sample": rep, "out": out, "sec": dt, **scenario.score(extract_json(out))}
jobs = [(k, c, r) for c in cases for r in range(a.repeat) for k in ("t1", "t2")] + [("t4", None, s) for s in range(a.t4_samples)]
with ThreadPoolExecutor(a.workers) as ex:
    for kind, row in ex.map(lambda j: job(*j), jobs): res[kind].append(row)
res["wall_sec"] = time.time() - t0
(ROOT / "results" / f"{a.label}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print("saved", a.label, "wall", round(res["wall_sec"]), "s")

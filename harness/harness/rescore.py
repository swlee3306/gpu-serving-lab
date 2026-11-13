"""저장된 원문 출력으로 T1/T2/T4를 다시 채점한다(채점기 수정 후 모델을 다시 돌리지 않기 위해). 바뀐 항목을 보고한다."""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoring, scenario
from llm import extract_json
ROOT = pathlib.Path(__file__).resolve().parent.parent
cases = {c["id"]: c for c in json.load(open(ROOT / "data" / "cases.json", encoding="utf-8"))}
for p in sorted((ROOT / "results").glob("*.json")):
    d = json.load(open(p, encoding="utf-8")); changed = {"t1": 0, "t2": 0, "t4": 0}
    for r in d["t1"]:
        n = scoring.score_t1(cases[r["id"]], r["out"])
        if any(r.get(k) != v for k, v in n.items()): changed["t1"] += 1
        r.update(n)
    for r in d["t2"]:
        n = scoring.score_t2(cases[r["id"]], r["out"])
        if any(r.get(k) != v for k, v in n.items()): changed["t2"] += 1
        r.update(n)
    for r in d["t4"]:
        n = scenario.score(extract_json(r["out"]))
        if any(r.get(k) != v for k, v in n.items()): changed["t4"] += 1
        r.update(n)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print(p.name, "재채점으로 바뀐 행:", changed)

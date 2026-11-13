import json, pathlib, random, sys
from collections import Counter, defaultdict
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoring, scenario
ROOT = pathlib.Path(__file__).resolve().parent.parent
cases = {c["id"]: c for c in json.load(open(ROOT / "data" / "cases.json", encoding="utf-8"))}


def pct(x, n): return f"{100 * x / n:.0f}% ({x}/{n})" if n else "-"


def consistency(rows):
    by = defaultdict(set)
    for r in rows: by[r["id"]].add(r.get("pred") if "pred" in r else r.get("cat"))
    return sum(1 for v in by.values() if len(v) == 1), len(by)


def summarize(path):
    d = json.load(open(path, encoding="utf-8"))
    t1 = d["t1"]; t2 = d["t2"]; t4 = d["t4"]
    out = {"label": d["label"], "model": d["model"]}
    out["t1_acc"] = pct(sum(r["correct"] for r in t1), len(t1)); out["t1_parse"] = pct(sum(r["parsed"] for r in t1), len(t1))
    per = defaultdict(lambda: [0, 0])
    for r in t1: per[cases[r["id"]]["label"]][1] += 1; per[cases[r["id"]]["label"]][0] += r["correct"]
    out["t1_by_cat"] = {k: f"{v[0]}/{v[1]}" for k, v in per.items()}
    # 반복 간 일관성(같은 입력, temperature 0)
    by = defaultdict(set)
    for r in t1: by[r["id"]].add(r["pred"])
    out["t1_consistent"] = f"{sum(1 for v in by.values() if len(v) == 1)}/{len(by)}"
    out["t2_schema"] = pct(sum(r["schema_ok"] for r in t2), len(t2))
    ok = [r for r in t2 if r["schema_ok"]]
    out["t2_category"] = pct(sum(r["category_ok"] for r in ok), len(ok))
    cnt = [r for r in ok if "counts_ok" in r]; out["t2_counts"] = pct(sum(r["counts_ok"] for r in cnt), len(cnt))
    out["t2_failed_tests"] = pct(sum(r["failed_tests_ok"] for r in ok), len(ok)); out["t2_criteria"] = pct(sum(r["criteria_ok"] for r in ok), len(ok))
    out["t2_hallucinated_names"] = sum(r["hallucinated_names"] for r in ok)
    p4 = [r for r in t4 if r.get("parsed")]
    out["t4_parsed"] = f"{len(p4)}/{len(t4)}"
    if p4:
        out["t4_valid_rate"] = pct(sum(r["valid"] for r in p4), sum(r["n"] for r in p4))
        out["t4_false_alarm"] = sum(r["false_alarm"] for r in p4); out["t4_malformed"] = sum(r["malformed"] for r in p4)
        out["t4_kills_per_sample"] = [r["kills"] for r in p4]; out["t4_kills_union"] = len({k for r in p4 for k in r["killed"]})
    out["wall_sec"] = round(d["wall_sec"])
    return out


if __name__ == "__main__":
    base = sum(scoring.rules_baseline(c) == c["label"] for c in cases.values())
    print(f"규칙 기준선(낙관적 상한): T1 정확도 {pct(base, len(cases))}")
    rng = random.Random(7); ks = [scenario.score(scenario.random_scenarios(rng))["kills"] for _ in range(200)]
    print(f"무작위 시나리오 기준선: 변이 {len(scenario.APPLICABLE)}개 중 평균 {sum(ks)/len(ks):.1f}개 탐지(10개 시나리오, 200회)")
    for p in sorted((ROOT / "results").glob("*.json")):
        if p.name.startswith("report"): continue
        print(json.dumps(summarize(p), ensure_ascii=False, indent=1))

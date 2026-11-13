import json, re
from jsonschema import Draft202012Validator

CATEGORIES = ["behavior_failure", "verification_incomplete", "check_weakened", "ok"]
T2_SCHEMA = {"type": "object", "required": ["category", "executed", "failed_tests", "criteria_changes", "summary"], "properties": {
    "category": {"enum": CATEGORIES},
    "executed": {"type": "object", "required": ["passed", "failed", "errors"], "properties": {k: {"type": "integer"} for k in ("passed", "failed", "errors")}},
    "failed_tests": {"type": "array", "items": {"type": "string"}}, "criteria_changes": {"type": "array", "items": {"type": "string"}}, "summary": {"type": "string"}}}
_v = Draft202012Validator(T2_SCHEMA)


def rules_baseline(c):
    """규칙 기준선: 이 데이터를 본 뒤에 작성한 낙관적 상한이다(처음 보는 로그에서 이만큼 나온다는 뜻이 아니다)."""
    log, diff = c["log"], c["diff"]
    if c["exit_code"] in (124, 2, 4, 5) or re.search(r"\b(\d+) errors?\b", log) or "no tests ran" in log or "ERROR collecting" in log:
        return "verification_incomplete"
    if re.search(r"\b\d+ failed\b", log) or "FAILED" in log:
        return "behavior_failure"
    if diff:
        removed = [l[1:] for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")]
        added = [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
        if any("def test_" in l for l in removed) and not any("def test_" in l for l in added) \
           or any("pytest.mark.skip" in l or "pytest.mark.xfail" in l for l in added) \
           or any(l.strip().startswith("assert") for l in removed):
            return "check_weakened"
    m = re.search(r"(\d+) passed", log); passed = int(m.group(1)) if m else 0
    sk = re.search(r"(\d+) skipped", log); skipped = int(sk.group(1)) if sk else 0
    if c["tested_commit"] != c["current_commit"] or passed + skipped < c["required_tests"]:
        return "verification_incomplete"
    return "ok"


def score_t1(case, out):
    from llm import extract_json
    j = extract_json(out)
    cat = j.get("category") if isinstance(j, dict) else None
    return {"parsed": cat in CATEGORIES, "pred": cat, "correct": cat == case["gt"]["category"]}


def score_t2(case, out):
    from llm import extract_json
    j = extract_json(out); gt = case["gt"]
    r = {"parsed": isinstance(j, dict), "schema_ok": False}
    if not r["parsed"]: return r
    r["schema_ok"] = _v.is_valid(j)
    if not r["schema_ok"]: return r
    r["category_ok"] = j["category"] == gt["category"]
    if gt["counts"] is not None:
        e = j["executed"]; r["counts_ok"] = all(e[k] == gt["counts"][k] for k in ("passed", "failed", "errors"))
    r["failed_tests_ok"] = set(j["failed_tests"]) == set(case_failed(case))
    r["criteria_ok"] = set(j["criteria_changes"]) == set(gt["diff_files"])
    allowed = set(re.findall(r"test_[A-Za-z0-9_]+", case["log"] + "\n" + case["diff"]))
    r["hallucinated_names"] = len([t for t in set(re.findall(r"test_[A-Za-z0-9_]+", j["summary"])) if t not in allowed])
    return r


def case_failed(case):
    """로그의 FAILED 줄에서 정답 실패 테스트 이름을 읽는다(실제 pytest 출력 기반)."""
    return sorted(set(re.findall(r"FAILED [^\s:]+::(\w+)", case["log"])))

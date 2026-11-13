"""평가 사례 생성. 모든 로그는 실제 pytest 실행 결과이며(표시한 synthetic 필드 제외), 정답은 넣은 오류의 종류로 정해진다."""
import difflib, json, pathlib, re, shutil, subprocess, sys, tempfile
import xml.etree.ElementTree as ET
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mutants import MUTANTS, mutate, source_text

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = (ROOT / "tests" / "test_ingest.py").read_text(encoding="utf-8")
COMMIT = "a1b2c3d"


def run_pytest(src_text, tests_text, args=(), conftest=None, timeout=60, test_path="tests"):
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d).resolve()
        (d / "src").mkdir(); (d / "tests").mkdir()
        (d / "src" / "ingest.py").write_text(src_text, encoding="utf-8")
        (d / "tests" / "test_ingest.py").write_text(tests_text, encoding="utf-8")
        (d / "pytest.ini").write_text("[pytest]\npythonpath = src\n", encoding="utf-8")
        if conftest: (d / "tests" / "conftest.py").write_text(conftest, encoding="utf-8")
        cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", "--no-header", "-p", "no:cacheprovider", "--junitxml=report.xml", *args, test_path]
        timed_out = False
        try:
            p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=timeout)
            rc, out = p.returncode, p.stdout
        except subprocess.TimeoutExpired as e:
            timed_out, rc = True, 124
            out = (e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or ""))
        counts = None
        x = d / "report.xml"
        if x.exists() and not timed_out:
            ts = ET.parse(x).getroot()
            ts = ts if ts.tag == "testsuite" else ts[0]
            tests, fail, err, skip = (int(ts.get(k, 0)) for k in ("tests", "failures", "errors", "skipped"))
            failed_names = sorted({tc.get("name") for tc in ts.iter("testcase") if tc.find("failure") is not None})
            counts = {"passed": tests - fail - err - skip, "failed": fail, "errors": err, "failed_tests": failed_names}
        out = out.replace(str(d), "<repo>")
        out = re.sub(r"in \d+\.\d+s", "in 0.01s", out)
        return rc, out.strip(), counts, timed_out


def unified(old, new, name):
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{name}", f"b/{name}", n=2)).strip()


def case(cid, label, rc, log, diff, counts, files, required=9, tested=COMMIT, current=COMMIT, cmd="pytest -q --tb=short tests", synthetic=False, note=""):
    return {"id": cid, "label": label, "exit_code": rc, "log": log, "diff": diff or "", "required_tests": required,
            "tested_commit": tested, "current_commit": current, "command": cmd,
            "gt": {"category": label, "counts": counts, "diff_files": files}, "synthetic_log": synthetic, "note": note}


def build():
    ref = source_text(); cases = []
    # A. 동작 실패: 변이 8종
    for mid, (desc, _r, _s) in MUTANTS.items():
        rc, log, cnt, _ = run_pytest(mutate(mid), TESTS)
        assert rc == 1 and cnt["failed"] >= 1, mid
        cases.append(case(f"A_{mid}", "behavior_failure", rc, log, "", {"passed": cnt["passed"], "failed": cnt["failed"], "errors": 0}, [], note=f"mutant {mid}: {desc}"))
    cnt_ok = lambda c: {"passed": c["passed"], "failed": c["failed"], "errors": c["errors"]}
    # B. 검증 불완전
    hang = ref.replace("    values = [v for", "    while True:\n        pass\n    values = [v for", 1)
    rc, log, cnt, to = run_pytest(hang, TESTS, timeout=5)
    cases.append(case("B_timeout", "verification_incomplete", 124, (log + "\n" if log else "") + "[CI] 명령이 5초 후 시간 초과로 종료됨 (프로세스 강제 종료)", "", None, [], note="hang injected, killed after 5s"))
    rc, log, cnt, _ = run_pytest(ref, "from ingest import Store, ingest, aggregate, save, SaveError, merge_batches\n" + TESTS)
    cases.append(case("B_collection_error", "verification_incomplete", rc, log, "", {"passed": 0, "failed": 0, "errors": cnt["errors"] if cnt else 1}, [], note="ImportError at collection"))
    rc, log, cnt, _ = run_pytest(ref, TESTS, args=("-k", "nonexistent_filter"))
    cases.append(case("B_no_tests_ran", "verification_incomplete", rc, log, "", {"passed": 0, "failed": 0, "errors": 0}, [], cmd="pytest -q --tb=short -k nonexistent_filter tests", note="-k matched nothing"))
    rc, log, cnt, _ = run_pytest(ref, TESTS, conftest="import pytest\n\n@pytest.fixture(autouse=True)\ndef _db():\n    raise ConnectionRefusedError('db:5432 connection refused')\n")
    cases.append(case("B_setup_error", "verification_incomplete", rc, log, "", {"passed": 0, "failed": 0, "errors": cnt["errors"]}, [], note="fixture cannot reach test DB"))
    rc, log, cnt, _ = run_pytest(ref, TESTS, test_path="tests/test_ingest_v2.py")
    cases.append(case("B_file_missing", "verification_incomplete", rc, log, "", None, [], cmd="pytest -q --tb=short tests/test_ingest_v2.py", note="required test file not found"))
    rc, log, cnt, _ = run_pytest(ref, TESTS, args=("-k", "boundary"))
    cases.append(case("B_subset_only", "verification_incomplete", rc, log, "", cnt_ok(cnt), [], cmd="pytest -q --tb=short -k boundary tests", note="only 3 of 9 required tests ran"))
    rc, log, cnt, _ = run_pytest(ref, TESTS)
    cases.append(case("B_stale_evidence", "verification_incomplete", rc, log, "", cnt_ok(cnt), [], tested="a1b2c3d", current="9f8e7d6", note="evidence is for an older commit"))
    # C. 검사 무력화 (테스트를 바꿔서 변이본을 통과시킴)
    def weakened(cid, mid, new_tests, note):
        rc, log, cnt, _ = run_pytest(mutate(mid), new_tests)
        assert rc == 0, (cid, log)
        cases.append(case(cid, "check_weakened", rc, log, unified(TESTS, new_tests, "tests/test_ingest.py"), cnt_ok(cnt), ["tests/test_ingest.py"], note=note))
    weakened("C_skip", "M7_dedupe_ignores_source", TESTS.replace("def test_same_ts_different_source_both_count", '@pytest.mark.skip(reason="flaky on CI")\ndef test_same_ts_different_source_both_count', 1), "skip added to the only killing test")
    weakened("C_expected_changed", "M2_no_dedupe", TESTS.replace("assert aggregate(s, 0, 10) == 30", "assert aggregate(s, 0, 10) == 60", 1), "expected value changed to match mutant output")
    t3 = re.sub(r"def test_save_is_atomic_on_failure\(\):\n(?:    .*\n)+\n\n", "", TESTS, count=1)
    weakened("C_test_deleted", "M5_no_rollback", t3, "killing test deleted")
    weakened("C_assertion_weakened", "M2_no_dedupe", TESTS.replace("assert aggregate(s, 0, 10) == 30", "assert aggregate(s, 0, 10) is not None", 1), "assertion weakened to not-None")
    weakened("C_xfail", "M8_swallow_failure", TESTS.replace("def test_save_is_atomic_on_failure", '@pytest.mark.xfail(reason="known issue")\ndef test_save_is_atomic_on_failure', 1), "xfail added to the only killing test")
    t6 = re.sub(r"assert aggregate\(s, (\d+), (\d+)\) == (\d+)", r"assert abs(aggregate(s, \1, \2) - \3) <= 2", TESTS)
    weakened("C_tolerance", "M6_sum_plus_one", t6, "tolerance widened on all sum assertions")
    # D. 정상
    rc, log, cnt, _ = run_pytest(ref, TESTS)
    cases.append(case("D_all_pass", "ok", rc, log, "", cnt_ok(cnt), [], note="reference, no change"))
    t_new = TESTS + "\n\ndef test_empty_store_is_none():\n    assert aggregate(Store(), 0, 10) is None\n"
    rc, log, cnt, _ = run_pytest(ref, t_new)
    cases.append(case("D_test_added", "ok", rc, log, unified(TESTS, t_new, "tests/test_ingest.py"), cnt_ok(cnt), ["tests/test_ingest.py"], required=10, note="legitimate new test added"))
    t_ref = TESTS.replace("def rec(", "def make_rec(").replace("rec(\"", "make_rec(\"")
    rc, log, cnt, _ = run_pytest(ref, t_ref)
    assert rc == 0, log
    cases.append(case("D_refactor", "ok", rc, log, unified(TESTS, t_ref, "tests/test_ingest.py"), cnt_ok(cnt), ["tests/test_ingest.py"], note="helper renamed, behavior same"))
    return cases


if __name__ == "__main__":
    cases = build()
    out = ROOT / "data" / "cases.json"
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(len(cases), "cases", dict(Counter(c["label"] for c in cases)))
    for c in cases:
        print(f"{c['id']:26s} {c['label']:24s} rc={c['exit_code']:<3} counts={c['gt']['counts']} log_lines={len(c['log'].splitlines())} diff_lines={len(c['diff'].splitlines())}")

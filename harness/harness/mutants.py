"""의도적 오류(변이). 기준 구현의 소스를 문자열 치환으로 바꾼다. id, 설명, 치환 규칙, 적용 가능한 시나리오 여부."""
import pathlib

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "ingest.py"

MUTANTS = {
    "M1_end_inclusive": ("끝 경계 포함(< → <=)", [("start <= ts < end", "start <= ts <= end")], True),
    "M2_no_dedupe": ("중복 제거 삭제", [("if key in store.seen:", "if False:")], True),
    "M3_missing_zero": ("결측을 0으로 반환", [("        return None\n    return sum(values)", "        return 0\n    return sum(values)")], True),
    "M4_start_exclusive": ("시작 경계 제외(<= → <)", [("start <= ts < end", "start < ts < end")], True),
    "M5_no_rollback": ("저장 실패 시 롤백 삭제", [("        store.results.clear()\n        store.results.update(snapshot)\n", "")], False),
    "M6_sum_plus_one": ("합계에 1 더함", [("    return sum(values)", "    return sum(values) + 1")], True),
    "M7_dedupe_ignores_source": ("중복 키에서 source 무시", [('key = (r["source"], r["ts"])', 'key = (r["ts"],)')], True),
    "M8_swallow_failure": ("저장 실패를 삼킴", [("        raise\n", "        return False\n")], False),
}


def source_text():
    return SRC.read_text(encoding="utf-8")


def mutate(mutant_id, text=None):
    text = source_text() if text is None else text
    for old, new in MUTANTS[mutant_id][1]:
        assert old in text, f"{mutant_id}: pattern not found: {old!r}"
        text = text.replace(old, new, 1)
    return text

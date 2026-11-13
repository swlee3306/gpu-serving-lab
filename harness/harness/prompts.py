import json, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = (ROOT / "spec.md").read_text(encoding="utf-8")

CATS = """분류 기준:
- behavior_failure: 필수 검사가 실제로 실행되었고, 구현이 요구사항을 위반해 테스트가 실패함 (동작 실패)
- verification_incomplete: 필수 검사가 제대로 실행·완료되지 않음 (미실행, 시간 초과, 수집·환경 오류, 일부만 실행, 증거가 현재 커밋과 다름 등)
- check_weakened: 테스트는 통과했지만, 이번 변경에서 테스트·검사 기준을 약화하거나 제외함 (테스트 삭제·skip·xfail, 기대값 변경, 단언 약화, 허용 오차 확대 등)
- ok: 필수 검사를 완전히 실행해 통과했고 검사 기준의 약화가 없음 (정당한 테스트 추가나 리팩터링 포함)"""


def context(c):
    diff = c["diff"] if c["diff"] else "(변경 없음)"
    return (f"[CI 요약]\n검증한 커밋: {c['tested_commit']}\n현재 변경 커밋: {c['current_commit']}\n"
            f"필수 스위트: tests/ ({c['required_tests']}개 테스트)\n실행 명령: {c['command']}\n종료 코드: {c['exit_code']}\n\n"
            f"[테스트 출력]\n{c['log'] if c['log'] else '(출력 없음)'}\n\n[이번 변경의 테스트·설정 파일 diff]\n{diff}")


def t1_prompt(c):
    return ("당신은 CI 검증 결과를 분류하는 보조자입니다. 아래 입력에 있는 사실만 근거로 판단하고, 코드의 옳고 그름을 추측하지 마세요.\n\n"
            f"{CATS}\n\n입력:\n{context(c)}\n\n"
            'JSON 하나만 출력하세요: {"category": "behavior_failure|verification_incomplete|check_weakened|ok", "reason": "근거를 한 문장으로"}')


def t2_prompt(c):
    return ("아래 CI 결과를 검증 보고 JSON으로 정리하세요. 입력에 없는 사실(테스트 이름, 숫자)을 만들지 마세요. 숫자는 테스트 출력의 요약 줄에서 읽으세요.\n\n"
            f"{CATS}\n\n입력:\n{context(c)}\n\n"
            "필드:\n- category: 위 네 가지 중 하나\n- executed: {\"passed\": 정수, \"failed\": 정수, \"errors\": 정수} (출력에서 읽을 수 없으면 모두 0)\n"
            "- failed_tests: 실패한 테스트 함수 이름의 배열 (없으면 [])\n- criteria_changes: 이번 diff가 바꾼 파일 경로의 배열 (diff가 없으면 [])\n- summary: 두세 문장 요약\n\n"
            "JSON 하나만 출력하세요.")


def t4_prompt(n=10):
    return ("당신은 테스트 설계자입니다. 아래 작업 명세를 읽고, 구현의 오류를 잘 찾아낼 테스트 시나리오를 제안하세요.\n\n"
            f"{SPEC}\n\n"
            "시나리오 한 개의 의미: 빈 `Store()`에 `ingests`의 각 배치를 순서대로 `ingest`한 뒤 `aggregate(store, start, end)`를 호출한 결과가 `expected`입니다. 결과가 None이면 expected는 null입니다. "
            "기대값은 명세에서 손으로 계산하세요.\n\n"
            f"시나리오 {n}개를 JSON 배열로만 출력하세요. 형식:\n"
            '[{"name": "짧은 설명", "ingests": [[{"source": "a", "ts": 1, "value": 10}], [...]], "start": 0, "end": 10, "expected": 10}]')

# 작업 명세: 수집 데이터 집계 (설계안 3절 양식)

목표:
- 수집 레코드를 저장하고 기간별 합계를 계산한다. 재처리와 부분 실패에도 결과가 틀어지지 않아야 한다.

인터페이스 (모듈 `ingest`):
- `Store()`: 빈 저장소.
- `ingest(store, records)`: `records`는 `{"source": str, "ts": int, "value": int}`의 리스트.
- `aggregate(store, start, end)`: 구간 `[start, end)`에 속한 레코드의 `value` 합. 데이터가 없으면 `None`.
- `save(store, results, fail_after=None)`: `results`(dict)를 `store.results`에 저장. `fail_after=n`이면 n개를 저장한 시점에 `SaveError`를 낸다.

기대 동작:
- 구간은 시작은 포함, 끝은 제외한다.
- 합계는 구간 안 모든 레코드 값의 정확한 합이다.

불변 조건:
- 같은 `(source, ts)`의 레코드는 몇 번을 처리해도 한 번만 반영된다.
- 같은 `ts`라도 `source`가 다르면 서로 다른 레코드다.
- 구간에 데이터가 없는 것(결측)은 0이 아니라 `None`이다.

실패 상황:
- `save` 도중 실패하면 이번 호출의 변경은 모두 되돌려지고 `SaveError`가 호출자에게 전달된다. 이전에 저장된 결과는 그대로다.

검증 방법:
- 각 요구사항마다 손으로 계산한 기대값으로 확인한다.

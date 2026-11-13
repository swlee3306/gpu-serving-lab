"""T4: 모델이 제안한 시나리오를 기준 구현과 변이본에서 실행해 채점한다."""
import random, types
from mutants import MUTANTS, mutate, source_text

APPLICABLE = [m for m, (_d, _r, ok) in MUTANTS.items() if ok]


def load_module(text):
    m = types.ModuleType("ingest_under_test"); exec(compile(text, "<ingest>", "exec"), m.__dict__); return m


REF = load_module(source_text())
MUT = {m: load_module(mutate(m)) for m in APPLICABLE}


def run_scenario(mod, sc):
    s = mod.Store()
    for batch in sc["ingests"]:
        mod.ingest(s, batch)
    return mod.aggregate(s, sc["start"], sc["end"])


def well_formed(sc):
    try:
        if not isinstance(sc["ingests"], list) or not all(isinstance(b, list) for b in sc["ingests"]): return False
        for b in sc["ingests"]:
            for r in b:
                if not (isinstance(r["source"], str) and isinstance(r["ts"], int) and isinstance(r["value"], int)): return False
        return isinstance(sc["start"], int) and isinstance(sc["end"], int) and (sc["expected"] is None or isinstance(sc["expected"], (int, float)))
    except Exception:
        return False


def score(scenarios):
    """valid: 기준 구현의 결과가 expected와 같은 시나리오(올바른 테스트). false_alarm: expected가 기준 구현과 다름(올바른 구현에서 실패할 테스트)."""
    if not isinstance(scenarios, list): return {"parsed": False}
    n = len(scenarios); malformed = valid = false_alarm = 0; valid_list = []
    for sc in scenarios:
        if not isinstance(sc, dict) or not well_formed(sc): malformed += 1; continue
        try: ref = run_scenario(REF, sc)
        except Exception: malformed += 1; continue
        if ref == sc["expected"]: valid += 1; valid_list.append((sc, ref))
        else: false_alarm += 1
    killed = set()
    for m in APPLICABLE:
        for sc, ref in valid_list:
            try: out = run_scenario(MUT[m], sc)
            except Exception: out = "EXC"
            if out != ref: killed.add(m); break
    return {"parsed": True, "n": n, "malformed": malformed, "valid": valid, "false_alarm": false_alarm, "killed": sorted(killed), "kills": len(killed)}


def random_scenarios(rng, n=10):
    out = []
    for _ in range(n):
        batches = []
        for _b in range(rng.randint(1, 3)):
            batches.append([{"source": rng.choice("ab"), "ts": rng.randint(0, 30), "value": rng.randint(1, 9)} for _ in range(rng.randint(1, 4))])
        start = rng.randint(0, 20); end = start + rng.randint(1, 15)
        sc = {"name": "random", "ingests": batches, "start": start, "end": end}
        sc["expected"] = run_scenario(REF, sc); out.append(sc)
    return out

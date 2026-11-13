"""검증 장치 자체의 검증(설계안 8절): 기준 구현은 모든 테스트를 통과하고, 각 변이본은 적어도 하나의 테스트에서 실패해야 한다."""
import pathlib, shutil, subprocess, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mutants import MUTANTS, mutate, source_text

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run_pytest(src_text, extra=("-q",)):
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / "src").mkdir(); (d / "tests").mkdir()
        (d / "src" / "ingest.py").write_text(src_text, encoding="utf-8")
        shutil.copy(ROOT / "tests" / "test_ingest.py", d / "tests" / "test_ingest.py")
        (d / "pytest.ini").write_text("[pytest]\npythonpath = src\n", encoding="utf-8")
        p = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--no-header", "--tb=line", *extra], cwd=d, capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout


if __name__ == "__main__":
    ref_rc, out = run_pytest(source_text())
    print("기준 구현:", "통과" if ref_rc == 0 else "실패", "|", out.strip().splitlines()[-1])
    survived = []
    for mid, (desc, _r, _s) in MUTANTS.items():
        rc, out = run_pytest(mutate(mid))
        killed = rc != 0
        failed = [l.split("::")[-1].split(" ")[0] for l in out.splitlines() if l.startswith("FAILED")]
        print(f"{mid:26s} {desc:22s} -> {'잡힘' if killed else '살아남음(검사 보강 필요)'} {failed}")
        if not killed: survived.append(mid)
    print("살아남은 변이:", survived or "없음")
    sys.exit(1 if survived or ref_rc != 0 else 0)

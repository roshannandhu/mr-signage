"""Run every isolated test script on its own, the way this project requires.

Each script builds its own throwaway database and imports `backend` after pointing
DATABASE_URL at it, so they cannot share a process. pytest runs them as subprocesses too, but
running many of them inside ONE pytest invocation spuriously fails the signup and quota scripts;
one process per script is the reliable shape, and it is what CI uses.

    python tests/run_isolated.py                       # all of them
    python tests/run_isolated.py test_plan_limits ...  # just these

The list comes from ISOLATED_SCRIPTS in tests/conftest.py, which pytest's orphan guard keeps
complete, so a new script cannot be left out of CI by forgetting to add it here.
"""
import os
import pathlib
import re
import subprocess
import sys
import time

TESTS = pathlib.Path(__file__).resolve().parent
ROOT = TESTS.parent
TIMEOUT_SECONDS = 600


def isolated_script_names() -> list[str]:
    source = (TESTS / "conftest.py").read_text(encoding="utf-8")
    block = source[source.index("ISOLATED_SCRIPTS = {"):source.index("PURE_MODULES = {")]
    return list(dict.fromkeys(re.findall(r'"(test_[a-z0-9_]+)\.py"', block)))


def run_one(name: str) -> tuple[bool, float, str]:
    started = time.time()
    try:
        result = subprocess.run(
            [sys.executable, str(TESTS / f"{name}.py")],
            cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        return False, time.time() - started, f"timed out after {TIMEOUT_SECONDS}s"
    tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-15:])
    return result.returncode == 0, time.time() - started, tail


def main() -> int:
    wanted = sys.argv[1:] or isolated_script_names()
    unknown = [name for name in wanted if name not in isolated_script_names()]
    if unknown:
        print(f"Not registered in ISOLATED_SCRIPTS: {', '.join(unknown)}")
        return 2

    failed = []
    for name in wanted:
        ok, seconds, tail = run_one(name)
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({seconds:.0f}s)", flush=True)
        if not ok:
            failed.append(name)
            print(tail, flush=True)

    print(f"\n{len(wanted) - len(failed)}/{len(wanted)} passed")
    if failed:
        print("failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""worker._with_heartbeat: a cron job's own result/exception is untouched, and its
heartbeat is written for Platform Health to read.

Pure logic -- a fake ctx with a fake Redis, no real database or Redis needed. Pins:
  * a successful job's return value passes through unchanged, and the heartbeat written
    says ok=True with a duration
  * a failing job's exception still propagates (the heartbeat wrapper must never swallow
    a real failure), and the heartbeat written says ok=False with the error
  * a Redis write failure does not suppress the job's own result or exception
"""
import asyncio
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "pytest-cron-heartbeats")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from backend.worker import _with_heartbeat  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


class FakeRedis:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple[str, str, int]] = []

    async def set(self, key, value, ex=None):
        if self.fail:
            raise ConnectionError("redis is down")
        self.calls.append((key, value, ex))


def run() -> None:
    # --- success: result passes through, heartbeat says ok ---------------------------------
    async def succeeds(ctx):
        return "the job's own result"

    redis = FakeRedis()
    result = asyncio.run(_with_heartbeat("a_job", succeeds)({"redis": redis}))
    check(result == "the job's own result", f"a successful job's return value should pass through: {result!r}")
    check(len(redis.calls) == 1, f"expected exactly one heartbeat write, got {len(redis.calls)}")
    if redis.calls:
        key, payload, ttl = redis.calls[0]
        check(key == "cron_last_run:a_job", f"heartbeat key wrong: {key}")
        body = json.loads(payload)
        check(body["ok"] is True and body["error"] is None, f"success heartbeat should say ok: {body}")
        check(isinstance(body["duration_ms"], int), f"duration_ms should be recorded: {body}")
        check(isinstance(ttl, int) and ttl > 0, f"heartbeat should carry a TTL: {ttl}")

    # --- failure: exception still propagates, heartbeat says ok=False ----------------------
    async def fails(ctx):
        raise RuntimeError("boom")

    redis2 = FakeRedis()
    raised = None
    try:
        asyncio.run(_with_heartbeat("b_job", fails)({"redis": redis2}))
    except RuntimeError as exc:
        raised = exc
    check(raised is not None and str(raised) == "boom", "a failing job's exception must still propagate")
    check(len(redis2.calls) == 1, f"expected a heartbeat even on failure, got {len(redis2.calls)}")
    if redis2.calls:
        body = json.loads(redis2.calls[0][1])
        check(body["ok"] is False and body["error"] == "boom", f"failure heartbeat wrong: {body}")

    # --- a dead Redis must not suppress the job's own result --------------------------------
    redis3 = FakeRedis(fail=True)
    result3 = asyncio.run(_with_heartbeat("c_job", succeeds)({"redis": redis3}))
    check(result3 == "the job's own result", "a heartbeat write failure must not swallow the job's result")

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Cron heartbeats: job results/exceptions pass through unchanged, heartbeats written, Redis failure is non-fatal")


if __name__ == "__main__":
    run()

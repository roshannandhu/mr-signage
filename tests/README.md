# Tests

Run commands from the repository root.

## What the suite needs

| Service | Needed by | Without it |
|---|---|---|
| PostgreSQL on `localhost:5432` | 16 scripts that create their own throwaway database | those scripts **skip** with a stated reason |
| Redis on `localhost:6379` | `validation.py`, and any check whose content must reach `status="ready"` | the transcode job is never enqueued, so a playlist syncs no items |
| `ffmpeg` on `PATH` | the two media pipeline tests | they **skip** |

Nothing above is mocked away on purpose: these are the services production runs on, and a
test that quietly passes without them proves less than one that says it did not run.
Everything that does not need them — the storage, rotation, rollout and release checks —
runs anywhere:

```bash
python -m pytest tests -q          # 26 pass with no services at all
docker compose up -d db redis      # then the remaining 16 run too
```

## Run everything

```bash
python -m pytest tests -q
```

`conftest.py` collects each backend script as its **own subprocess**. That is
deliberate: `backend/database.py` builds the SQLAlchemy engine at import time from
`DATABASE_URL`, so running the scripts in one shared process makes the first import
win and the rest query a torn-down database — every script passes alone and the
suite fails. One process per script keeps each engine bound to its own database.

Because of that, these files must **not** define a `test_*` function; pytest would
import them into the shared process and run them a second time. Each keeps an
`if __name__ == "__main__"` block so it stays directly runnable.

Pure-logic tests are the exception. They touch no database and bind no engine, so they are
imported normally: list them in `python_files` in `pytest.ini` and in `PURE_MODULES` in
`conftest.py`. Every other file goes in `ISOLATED_SCRIPTS`.

**A file in neither registry is never run.** Six of them sat in that state — written,
committed, and silently skipped while the suite reported success. `pytest_collection_finish`
now compares the directory against both registries and fails the run if anything is
unaccounted for, so the omission cannot recur.

## Tenant isolation probe

`test_tenant_isolation.py` builds two organisations and calls every admin route as
org A against org B's ids, asserting 401/403/404 on all of them, plus that listings
never disclose foreign rows, that A cannot attach B's media to its own playlist, and
that a paired TV never syncs another organisation's playlist. A `422` is reported as
an *invalid probe* rather than a pass, so a malformed payload can never hide a real
leak behind schema validation.

```bash
python tests/test_tenant_isolation.py
```

## Quota enforcement

`test_quotas.py` covers the plan limits (GOAL.md T6.1/T6.2): pairing past
`max_screens` returns 409 with an upgrade message, an upload past the storage quota
returns 413, an in-quota upload still succeeds, and raising the quota immediately
unblocks the previously rejected upload.

```bash
python tests/test_quotas.py
```

## Feature parity check

`test_feature_parity.py` uses a disposable SQLite database and verifies real-user authentication, role enforcement, screen groups, scheduled playlist items, assignment inheritance, and `204 Not Modified` sync behavior.

```bash
pip install -r backend/requirements-dev.txt
python tests/test_feature_parity.py
```

## Storage and failure-path validation

`validation.py` runs FastAPI in-process with Moto-backed S3. It covers upload, signed sync URLs, pairing expiry, and missing-resource handling without touching the working database.

```bash
python tests/validation.py
```

## Live E2E test

`e2e_test.py` drives a running backend over `http://localhost:8000` (override with
`MR_BASE_URL`). Use an existing owner or editor account:

```bash
set MR_TEST_USERNAME=owner
set MR_TEST_PASSWORD=your-password
python tests/e2e_test.py
```

Step 8 downloads the uploaded file from the URL the API returns, which is built from
`PUBLIC_BASE_URL`. If the server runs on a non-default port, start it with
`PUBLIC_BASE_URL` matching, or that step fails while everything else passes.

The live test creates records and uploads a fixture to the running environment; the isolated tests do not.

"""No storage credential is ever written into tracked source.

This exists because "thumbnail not showing" kept coming back, and every time the cause was
the same: somebody made media work by hardcoding an object-storage key somewhere and
signing URLs next to it. It has happened in a public backend module and in the browser
bundle, where the credentials were served to every visitor.

The fix each time was to delete the signer and let the backend's `/api/media/<key>` do it,
which signs fresh on every request and cannot expire or drift. That fix does not stick on
its own -- hardcoding a key makes images appear immediately, so it keeps getting re-added
under pressure, and it keeps breaking again the moment the key rotates or a signature ages
out. This test is what makes it stick.

Pure logic: reads files, touches nothing.
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# A credential is one of these names given a literal value.
CREDENTIAL_NAMES = (
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_ACCESS_KEY_ID",
)

# `NAME = "literal"`, `"NAME": "literal"` and `os.environ["NAME"] = "literal"` cover Python
# dicts, Python constants, environment assignment and the TS/JS `const NAME = '...'`. The
# optional `]` is what catches the environ subscript form, which this used to miss. An
# unquoted right-hand side is a lookup, not a secret.
ASSIGNMENT = re.compile(
    r"(?P<name>" + "|".join(CREDENTIAL_NAMES) + r")"
    r"\"?\]?\s*[:=]\s*"
    r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)"
)

# Values that are obviously not a real key. A real R2 key id is 32 hex characters and a
# secret is 64, so these never collide with one.
PLACEHOLDERS = {"", "mock", "test", "testing", "changeme", "your-key", "your-secret", "none", "null"}

# A credential NAME mapped to the name of somewhere a credential is kept is not a credential.
# `{"AWS_ACCESS_KEY_ID": "storage.aws_access_key_id"}` is a lookup table, and flagging it
# would push whoever hit it towards weakening this guard rather than fixing anything. Kept
# deliberately narrow -- a dotted lower-case settings path and nothing else, so it can never
# be stretched to cover an actual key.
SETTINGS_PATH = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

# Config files are where credentials are SUPPOSED to live, and .env is gitignored anyway.
# This file names the patterns it hunts for, so it would match itself.
SKIP_NAMES = {"test_no_embedded_credentials.py"}
SKIP_SUFFIXES = (".env", ".env.example", ".env.local", ".env.sample")

TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".kt", ".java", ".json", ".yml", ".yaml"}


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    files = []
    for rel in out:
        path = REPO / rel
        if path.name in SKIP_NAMES or rel.endswith(SKIP_SUFFIXES):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        # Vendored trees are not ours to police and are enormous.
        if any(part in {"node_modules", "venv", "venv_old_broken", ".next"} for part in path.parts):
            continue
        files.append(path)
    return files


def find_embedded_credentials() -> list[str]:
    offences = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not any(name in text for name in CREDENTIAL_NAMES):
            continue
        for match in ASSIGNMENT.finditer(text):
            value = match.group("value").strip()
            if value.lower() in PLACEHOLDERS or value.lower().startswith("mock"):
                continue
            # A lookup that happens to be quoted, e.g. os.getenv("AWS_ACCESS_KEY_ID").
            if value.upper() in CREDENTIAL_NAMES:
                continue
            # A map from the credential's name to where that credential is STORED.
            if SETTINGS_PATH.match(value):
                continue
            line = text[: match.start()].count("\n") + 1
            offences.append(
                f"{path.relative_to(REPO).as_posix()}:{line} assigns {match.group('name')} "
                f"a literal value"
            )
    return offences


def test_no_storage_credentials_in_source():
    offences = find_embedded_credentials()
    assert not offences, (
        "Storage credentials are hardcoded in tracked source:\n  "
        + "\n  ".join(offences)
        + "\n\nPut them in the environment instead (Render -> Environment for production, "
        "backend/.env locally). Media resolves through the backend's /api/media/<key>, "
        "which signs a fresh URL per request -- nothing else needs a key, and anything "
        "shipped to the browser is public. If this is how you are trying to fix blank "
        "thumbnails, the key is not the fix: see resolve_media_url in backend/media_urls.py."
    )


def test_the_browser_bundle_signs_nothing():
    """The dashboard must not carry an S3/R2 signer at all.

    Separate from the credential check because a signer with the key moved one file away is
    the same bug wearing a hat -- and this is the specific shape that produced 7-day
    presigned URLs in the browser, which went blank a week later.
    """
    frontend = REPO / "frontend" / "src"
    offenders = []
    for path in frontend.rglob("*"):
        if path.suffix.lower() not in {".ts", ".tsx", ".js", ".jsx"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "aws4_request" in text or "X-Amz-Signature" in text:
            offenders.append(path.relative_to(REPO).as_posix())
    assert not offenders, (
        "The browser bundle contains an object-storage signer: " + ", ".join(offenders)
        + "\n\nThe API already returns absolute /api/media/<key> URLs (see "
        "ContentResponse.absolutise_urls); the browser has nothing to sign."
    )


def test_the_scan_catches_a_credential_assigned_through_os_environ():
    """The subscript form `os.environ["NAME"] = "literal"` slipped past the first pattern."""
    sample = (
        'os.environ["AWS_SECRET_ACCESS_KEY"] = "AbCd1234realsecret"\n'
        'os.environ["AWS_ACCESS_KEY_ID"] = "mock_key"\n'
    )
    values = [m.group("value") for m in ASSIGNMENT.finditer(sample)]
    assert values == ["AbCd1234realsecret", "mock_key"]


if __name__ == "__main__":
    test_no_storage_credentials_in_source()
    test_the_browser_bundle_signs_nothing()
    test_the_scan_catches_a_credential_assigned_through_os_environ()
    print("no embedded credentials: all checks passed")

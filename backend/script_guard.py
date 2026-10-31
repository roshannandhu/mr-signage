"""Confirmation for operator scripts that delete or overwrite data.

Several scripts under scripts/ (and reset_fresh_start.py) import backend.database, which loads
backend/.env -- the live database on a developer machine. Run by accident, or from the wrong
shell, they wiped or rewrote production with no prompt. A script that does that now calls
confirm_destructive() first: against SQLite or a database on this machine it does nothing, and
against a hosted one it needs `--yes` on the command line AND the database host typed back.

Standalone on purpose: no backend imports, so it works before the app is importable.
"""
from __future__ import annotations

import sys
from typing import Callable, Optional, Sequence

from sqlalchemy.engine import make_url

_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}


def confirm_destructive(
    action: str,
    db_url: str,
    *,
    argv: Optional[Sequence[str]] = None,
    ask: Callable[[str], str] = input,
) -> None:
    """Return if it is safe to continue, otherwise exit the process with a refusal."""
    host = (make_url(db_url).host or "").lower()
    if host in _LOCAL_HOSTS:
        return

    args = list(sys.argv[1:] if argv is None else argv)
    if "--yes" not in args:
        sys.exit(
            f"REFUSED: this would {action}\n"
            f"  on the HOSTED database {host}.\n"
            "  If that is really what you want, run it again with --yes and type the host "
            "name when asked."
        )

    try:
        typed = ask(f"This will {action}\n  on the HOSTED database {host}.\nType the host to continue: ")
    except EOFError:
        typed = ""
    if typed.strip().lower() != host:
        sys.exit("REFUSED: the host typed did not match. Nothing was changed.")

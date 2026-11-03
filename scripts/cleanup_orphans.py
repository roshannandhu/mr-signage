"""Find upload files that no database row points at.

Reports by default and changes nothing. Deleting media cannot be undone, so removal needs
an explicit --apply.

    python scripts/cleanup_orphans.py            # list what would go, delete nothing
    python scripts/cleanup_orphans.py --apply    # actually delete them

Orphans arise whenever a file outlives its row. The main source — content deletion leaving
its four transcoded renditions behind — is fixed in delete_content, and screenshots beyond
the retention limit are now pruned with their files, so this is a safety net rather than
routine housekeeping.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.database import SessionLocal  # noqa: E402
from backend import models  # noqa: E402
from backend.routers.content import UPLOAD_DIR  # noqa: E402
from _referenced_files import extra_stored_urls  # noqa: E402


def referenced_paths(db) -> set[str]:
    """Every upload path any row still points at, relative to the uploads root."""
    referenced: set[str] = set()

    def note(stored_url: str | None) -> None:
        if stored_url and "/uploads/" in stored_url:
            relative = stored_url.split("/uploads/", 1)[1]
            # Normalise separators so Windows paths compare equal to stored URLs.
            referenced.add(str(Path(relative)))

    for content in db.query(models.Content).all():
        note(content.file_url)
        note(content.thumbnail)
    for rendition in db.query(models.MediaRendition).all():
        note(rendition.file_url)
    for stored_url in extra_stored_urls(db):
        note(stored_url)
    return referenced


def find_orphans(db) -> list[Path]:
    root = Path(UPLOAD_DIR)
    if not root.exists():
        return []
    keep = referenced_paths(db)
    return [
        path for path in sorted(root.rglob("*"))
        if path.is_file() and str(path.relative_to(root)) not in keep
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="delete the orphans (irreversible)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        orphans = find_orphans(db)
        total = sum(path.stat().st_size for path in orphans)

        if not orphans:
            print("No orphaned files. Nothing to do.")
            return 0

        print(f"{len(orphans)} orphaned file(s), {total / 1024 / 1024:.1f} MB:")
        for path in orphans[:40]:
            print(f"  {path.relative_to(Path(UPLOAD_DIR))}  ({path.stat().st_size / 1024:.0f} KB)")
        if len(orphans) > 40:
            print(f"  ... and {len(orphans) - 40} more")

        if not args.apply:
            print("\nReport only — nothing was deleted.")
            print("Re-run with --apply to remove them.")
            return 0

        from backend.database import SQLALCHEMY_DATABASE_URL
        from backend.script_guard import confirm_destructive

        confirm_destructive("DELETE local upload files that the database does not reference", SQLALCHEMY_DATABASE_URL)

        removed = 0
        freed = 0
        for path in orphans:
            try:
                size = path.stat().st_size
                path.unlink()
                removed += 1
                freed += size
            except OSError as exc:
                print(f"  could not delete {path.name}: {exc}")
        print(f"\nDeleted {removed} file(s), freed {freed / 1024 / 1024:.1f} MB.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

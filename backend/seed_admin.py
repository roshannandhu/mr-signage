import argparse
import getpass

from . import database, models
from .routers.auth import get_or_create_default_organization, get_password_hash

# Roles this command may create. `super_admin` is the platform operator: the only role
# permitted to publish a player release, which installs across every tenant's fleet.
# There is deliberately no HTTP route that mints one -- the team page is capped at
# `owner` -- so creating one requires shell access to the host running the backend.
SEEDABLE_ROLES = ("owner", "super_admin")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an MR Signage account that cannot be created through the API"
    )
    parser.add_argument("username")
    parser.add_argument(
        "--email",
        default=None,
        help=(
            "Address this account signs in with. Worth setting for a super_admin: platform "
            "status now comes from this row's role, so Google sign-in has to be able to "
            "match the account by address."
        ),
    )
    parser.add_argument(
        "--role",
        choices=SEEDABLE_ROLES,
        default="owner",
        help=(
            "owner: the first account of a tenant organisation (default). "
            "super_admin: the platform operator who publishes player releases."
        ),
    )
    args = parser.parse_args()
    password = getpass.getpass("Password (minimum 8 characters): ")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters")

    models.Base.metadata.create_all(bind=database.engine)
    db = database.SessionLocal()
    try:
        if db.query(models.User).filter(models.User.username == args.username).first():
            raise SystemExit("That username already exists")
        organization = get_or_create_default_organization(db)
        user = models.User(
            organization_id=organization.id,
            username=args.username,
            email=args.email,
            hashed_password=get_password_hash(password),
            role=args.role,
            is_active=True,
        )
        db.add(user)
        db.commit()
        print(f"Created {args.role} account: {args.username}")
        if args.role == "super_admin":
            print(
                "This account is a platform operator: it signs in to /admin, approves "
                "companies, sets packages and publishes player releases to every tenant's "
                "fleet. It is hidden from the team page and cannot be edited there. "
                "This command is the ONLY way to create one -- the role is refused on "
                "every HTTP route, and the hardcoded email allow-lists it used to be "
                "granted by have been removed."
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()

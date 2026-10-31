import logging
import os
from datetime import datetime, timedelta
from typing import Callable

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import database, google_device, models, schemas
from ..limiter import limiter

router = APIRouter()

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


def get_secret_key() -> str:
    secret = os.getenv("SECRET_KEY")
    if not secret:
        raise RuntimeError("SECRET_KEY must be set in the backend environment")
    return secret


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def get_password_hash(password: str) -> str:
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > 72:
        raise ValueError("Password must be at most 72 UTF-8 bytes")
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = models.utcnow() + expires_delta
    else:
        expire = models.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, get_secret_key(), algorithm=ALGORITHM)


@router.post("/token", response_model=schemas.TokenResponse)
@limiter.limit("30/minute")
def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(database.get_db),
):
    from sqlalchemy import func
    login_id = (form_data.username or "").strip().lower()
    user = (
        db.query(models.User)
        .filter(
            (func.lower(models.User.username) == login_id)
            | (func.lower(models.User.email) == login_id)
        )
        .first()
    )
    if not user or not user.is_active or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer", "user": user}


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(database.get_db),
) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, get_secret_key(), algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not isinstance(username, str):
            raise credentials_exception
    except (JWTError, RuntimeError):
        raise credentials_exception

    user = db.query(models.User).filter(models.User.username == username).first()
    if not user or not user.is_active:
        raise credentials_exception
    return user


async def get_current_user_ws(
    token: str,
    db: Session
) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials"
    )
    try:
        payload = jwt.decode(token, get_secret_key(), algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not isinstance(username, str):
            raise credentials_exception
    except (JWTError, RuntimeError):
        raise credentials_exception

    user = db.query(models.User).filter(models.User.username == username).first()
    if not user or not user.is_active:
        raise credentials_exception
    return user


def require_roles(*roles: str) -> Callable:
    def dependency(user: models.User = Depends(get_current_user)) -> models.User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return dependency


@router.get("/methods")
def auth_methods():
    """Which sign-in buttons the dashboard should draw.

    The TV has its own copy of this at /api/screens/auth-methods; they are deliberately
    separate because they answer about different OAuth clients. A deployment can perfectly
    well have the browser half configured and not the TV half, or the reverse.
    """
    return {"google": google_device.is_web_configured(), "password": True}


@router.get("/google/url")
def get_google_auth_url(redirect_uri: str = "http://localhost:3000/login", state: str = None):
    """Where to send the browser to sign in with Google, or null if unavailable.

    The `else` branch here used to point at a local /google/oauth-page that rendered a fake
    Google account chooser built from real rows in the users table -- see the deletion
    above. A deployment with no Google credentials now says so by answering null, and the
    dashboard hides the button rather than starting a flow that cannot finish.
    """
    if not google_device.is_web_configured():
        return {"url": None}
    return {"url": google_device.build_oauth_url(redirect_uri, state)}


@router.post("/google", response_model=schemas.TokenResponse)
@limiter.limit("10/minute")
def sign_in_with_google(
    request: Request,
    body: schemas.GoogleWebSignInRequest,
    db: Session = Depends(database.get_db),
):
    """Sign in to the dashboard with a Google account, or sign up for a new workspace.

    Google authenticates a person; it does not authorise them. That distinction is the
    whole design here:

      - A verified address that already belongs to an MR Signage user signs that user in.
      - One that does not gets a NEW organisation, created with status="pending_approval"
        and no approved_at, plus an owner account inside it. Both exist immediately, but
        get_tenant_scope refuses every tenant route while the organisation sits in that
        status, so the account can see the pending screen and nothing else until a
        platform operator approves it from /admin/approvals.

    This docstring used to end "This route therefore creates nothing", which stopped being
    true when self-signup was added and is worth stating plainly rather than leaving a
    comment that reads as a security guarantee it no longer makes. Nothing is unguarded --
    the gate simply moved from "cannot sign up" to "signs up into a workspace that can do
    nothing until approved".
    """
    # An authorization code is the ONLY accepted input. This used to read:
    #
    #     if not is_web_configured() or (body.code and "@" in body.code):
    #         if "@" in body.code:
    #             claims = {"email": body.code.lower(), "email_verified": True, ...}
    #
    # which trusted any string containing an "@" as a verified Google identity -- and the
    # second half of that condition fired even when Google WAS correctly configured. A
    # single unauthenticated request, `{"code": "victim@example.com"}`, returned a signed
    # seven-day token for that account. Total authentication bypass; there is no
    # configuration in which it was safe.
    #
    # An unconfigured deployment now refuses rather than improvising an identity. The
    # password route still works, which is what a deployment without Google credentials is
    # expected to use.
    if not google_device.is_web_configured():
        raise HTTPException(status_code=503, detail="Google sign-in is not enabled on this server")
    try:
        claims = google_device.exchange_code(body.code, body.redirect_uri)
    except google_device.GoogleError as error:
        logging.getLogger(__name__).warning("Google web exchange failed: %s", error)
        raise HTTPException(status_code=502, detail="Could not complete Google sign-in. Try again.")

    google_sub = claims.get("sub")
    email = (claims.get("email") or "").strip().lower()
    if not email or not claims.get("email_verified", True):
        raise HTTPException(status_code=403, detail="That Google account has no verified email address.")

    import secrets
    user = None
    if google_sub:
        user = db.query(models.User).filter(models.User.google_sub == google_sub).first()
    if not user and email:
        user = (
            db.query(models.User)
            .filter((func.lower(models.User.email) == email) | (func.lower(models.User.username) == email))
            .first()
        )

    if user:
        if google_sub and not user.google_sub:
            user.google_sub = google_sub
        if claims.get("picture"):
            user.picture = claims.get("picture")
        user.auth_provider = "google"
        db.commit()
    else:
        # Every new workspace queues for approval, with no exceptions carved out by
        # address. This used to auto-activate three hardcoded emails, which was the third
        # copy of a super-admin list that had already drifted from the other two -- and it
        # meant anyone who managed to claim one of those addresses skipped the approval
        # gate entirely. Platform operators are now minted by
        # `python -m backend.seed_admin <name> --role super_admin`, not by signing up.
        org_name = claims.get("name") or email.split("@")[0]
        organization = models.Organization(
            name=f"{org_name}'s Workspace",
            slug=f"org-{secrets.token_hex(4)}",
            status="pending_approval",
            approved_at=None,
        )
        db.add(organization)
        db.flush()
        
        user = models.User(
            organization_id=organization.id,
            username=email,
            email=email,
            google_sub=google_sub,
            picture=claims.get("picture"),
            auth_provider="google",
            full_name=claims.get("name") or org_name,
            role="owner",
            is_active=True,
            hashed_password=get_password_hash(secrets.token_hex(16)),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logging.getLogger(__name__).info("New Google signup for %s (Org ID %s, Status %s).", email, organization.id, organization.status)

    logging.getLogger(__name__).info("Google web sign-in for %s", user.username)
    return {
        "access_token": create_access_token(data={"sub": user.username}),
        "token_type": "bearer",
        "user": user,
    }


@router.get("/me", response_model=schemas.UserResponse)
def read_current_user(user: models.User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=schemas.UserResponse)
def update_current_user(
    patch: schemas.ProfileUpdate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(database.get_db),
):
    """Self-service profile edit, for the account menu.

    Separate from PUT /api/users/{id}, which is owner-gated because it can change role and
    is_active. Anyone may edit their own display name and email; nobody gets to change
    their own privileges here.
    """
    updates = patch.model_dump(exclude_unset=True)
    if "email" in updates and updates["email"] is not None:
        updates["email"] = str(updates["email"])
        # Compared case-insensitively, because /auth/token matches the login identifier
        # with func.lower(). A case-sensitive uniqueness check let "Bob@x.com" and
        # "bob@x.com" both exist, after which logging in as either picked whichever row
        # .first() happened to return.
        clash = (
            db.query(models.User)
            .filter(
                func.lower(models.User.email) == updates["email"].lower(),
                models.User.id != user.id,
            )
            .first()
        )
        if clash:
            raise HTTPException(status_code=409, detail="That email is already in use")

    for field, value in updates.items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
def change_own_password(
    request: Request,
    payload: schemas.PasswordChange,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(database.get_db),
):
    """Change your own password.

    Requires the current password. PUT /api/users/{id} can also set a password but is
    owner-only and verifies nothing, so it is an admin reset, not a self-service change --
    without this route a non-owner could never rotate their own credentials, and a
    borrowed session could change the password of whoever was logged in.
    """
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=403, detail="Current password is incorrect")

    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="New password must differ from the current one")

    user.hashed_password = get_password_hash(payload.new_password)
    db.commit()
    return None


def get_or_create_default_organization(db: Session) -> models.Organization:
    organization = db.query(models.Organization).filter(models.Organization.slug == "default").first()
    if not organization:
        organization = models.Organization(name="Default Organization", slug="default")
        db.add(organization)
        db.flush()
    return organization


def ensure_initial_owner(db: Session) -> None:
    """Create the first real owner only when explicit bootstrap credentials are supplied."""
    username = os.getenv("INITIAL_ADMIN_USERNAME")
    password = os.getenv("INITIAL_ADMIN_PASSWORD")
    if not username or not password or db.query(models.User).count() > 0:
        return
    if len(password) < 8:
        raise RuntimeError("INITIAL_ADMIN_PASSWORD must be at least 8 characters")

    organization = get_or_create_default_organization(db)

    db.add(
        models.User(
            organization_id=organization.id,
            username=username,
            hashed_password=get_password_hash(password),
            role="owner",
            is_active=True,
        )
    )
    db.commit()

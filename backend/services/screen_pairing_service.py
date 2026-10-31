"""Screen Hardware Pairing and Enrollment Application Service."""

import logging
import secrets
import string
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from .base import BaseService
from ..repositories.screen_repo import ScreenRepository
from .. import models

logger = logging.getLogger(__name__)


def generate_pair_code() -> str:
    """Generate a 6-digit numeric pairing code for screen display.

    From `secrets`, not `random`: the Mersenne Twister is predictable from its past output,
    and these codes are the only thing standing between a signed-in tenant and an unclaimed
    TV that is not theirs.
    """
    return "".join(secrets.choice(string.digits) for _ in range(6))


def as_aware_utc(value: datetime) -> datetime:
    """Ensure datetime has explicit UTC timezone awareness."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ScreenPairingService(BaseService):
    """Application Service managing pairing codes, onboarding tokens, and hardware admission."""

    def __init__(self, db: Session, repo: Optional[ScreenRepository] = None):
        super().__init__(db)
        self.repo = repo or ScreenRepository(db)

    def generate_code(self) -> str:
        return generate_pair_code()

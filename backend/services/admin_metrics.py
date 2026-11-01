"""Platform-wide numbers for the admin console, computed in bulk.

Production pays ~114 ms per query, so everything here is one grouped query per measure
regardless of how many tenants there are -- never a query per tenant.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models
from ..alerting import offline_alert_after


def online_cutoff(now: Optional[datetime] = None) -> datetime:
    """A screen last heard from after this is online. The one admin definition."""
    return (now or models.utcnow()) - offline_alert_after()


def screen_is_online(last_seen: Optional[datetime], now: Optional[datetime] = None) -> bool:
    if last_seen is None:
        return False
    cutoff = online_cutoff(now)
    # SQLite hands back naive datetimes, Postgres aware ones.
    if last_seen.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=None)
    return last_seen >= cutoff


def fleet_screen_filter():
    """Screens an operator monitors: paired to a tenant, not archived, not awaiting pairing."""
    return (
        models.Screen.deleted_at.is_(None),
        models.Screen.organization_id.isnot(None),
        models.Screen.status != "waiting_pairing",
    )


@dataclass
class TenantUsage:
    owner: Optional[models.User] = None
    screens: int = 0
    online_screens: int = 0
    ad_slots_used: int = 0
    clients: int = 0
    storage_bytes: int = 0


def _grouped_counts(db: Session, column, org_ids, *filters) -> Dict[int, int]:
    rows = (
        db.query(column, func.count())
        .filter(column.in_(org_ids), *filters)
        .group_by(column)
        .all()
    )
    return {org_id: int(count) for org_id, count in rows}


def tenant_usage(db: Session, org_ids: Iterable[int]) -> Dict[int, TenantUsage]:
    """Usage for many tenants in a fixed number of queries.

    Counted exactly as the quotas are enforced: archived screens and unclaimed pairings do
    not occupy a slot, an ad holds its slot until its effective end, and storage is content
    plus renditions.
    """
    ids = list(org_ids)
    usage = {org_id: TenantUsage() for org_id in ids}
    if not ids:
        return usage

    owners = (
        db.query(models.User)
        .filter(models.User.organization_id.in_(ids), models.User.role == "owner")
        .order_by(models.User.created_at)
        .all()
    )
    for owner in owners:
        entry = usage[owner.organization_id]
        if entry.owner is None:
            entry.owner = owner

    screen = models.Screen
    for org_id, count in _grouped_counts(
        db, screen.organization_id, ids, screen.deleted_at.is_(None), screen.status != "waiting_pairing",
    ).items():
        usage[org_id].screens = count
    for org_id, count in _grouped_counts(
        db, screen.organization_id, ids, screen.deleted_at.is_(None),
        screen.status != "waiting_pairing", screen.last_seen >= online_cutoff(),
    ).items():
        usage[org_id].online_screens = count

    placement = models.AdPlacement
    for org_id, count in _grouped_counts(
        db, placement.organization_id, ids, placement.effective_ends_at >= models.utcnow(),
    ).items():
        usage[org_id].ad_slots_used = count

    for org_id, count in _grouped_counts(db, models.Client.organization_id, ids).items():
        usage[org_id].clients = count

    from .storage_service import storage_used_by_organization

    for org_id, used in storage_used_by_organization(db, ids).items():
        usage[org_id].storage_bytes = used
    return usage

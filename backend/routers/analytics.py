import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, text
from sqlalchemy.orm import Session

from ..database import get_db
from ..tenancy import TenantScope, ensure_feature, require_feature, require_tenant_roles
from ..models import Campaign, Content, PlayLogHourlyRollup, Screen, ScreenGroup

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _bucket_daily(rows) -> list[tuple]:
    """Hourly rollups folded into calendar days, oldest first.

    Done in Python because `date_trunc` is Postgres-only, and these endpoints also run on
    the SQLite the test suite and local development use -- where it raises "no such
    function" and 500s the whole response. placements.py bucket the same way for the same
    reason, and worker.py keeps a Postgres fast path only because it aggregates the entire
    table; these two queries are already narrowed to one campaign or one creative over a
    week or a month, so the row count is small and one portable path beats two.

    Takes (date_hour, total, completed) rows and returns (date, total, completed).
    """
    buckets: dict = {}
    for date_hour, total, completed in rows:
        day = date_hour.date()
        running = buckets.setdefault(day, [0, 0])
        running[0] += total or 0
        running[1] += completed or 0
    return [(day, totals[0], totals[1]) for day, totals in sorted(buckets.items())]


@router.get("/campaigns")
def list_campaigns(
    tenant: TenantScope = Depends(require_tenant_roles("owner", "editor", "viewer")),
    db: Session = Depends(get_db),
):
    campaigns = db.query(Campaign).filter(Campaign.organization_id == tenant.organization_id).all()
    return [{"id": c.id, "name": c.name} for c in campaigns]


@router.get("/campaigns/{campaign_id}")
def get_campaign_info(
    campaign_id: int,
    tenant: TenantScope = Depends(require_tenant_roles("owner", "editor", "viewer")),
    db: Session = Depends(get_db),
):
    campaign = db.query(Campaign).filter(
        Campaign.organization_id == tenant.organization_id,
        Campaign.id == campaign_id
    ).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # To get currently playing / online / offline, we look at screens assigned to this campaign.
    # We resolve assigned screens via effective_playlist_id matching a playlist in this campaign.
    screens = db.query(Screen).filter(Screen.organization_id == tenant.organization_id).all()
    
    # Pre-fetch playlists belonging to this campaign
    campaign_playlist_ids = {p.id for p in campaign.playlists}
    
    assigned_screens = [s for s in screens if s.effective_playlist_id in campaign_playlist_ids]
    
    online = sum(1 for s in assigned_screens if s.status == "online")
    offline = sum(1 for s in assigned_screens if s.status == "offline")
    playing = sum(1 for s in assigned_screens if s.playback_state == "playing")

    return {
        "id": campaign.id,
        "name": campaign.name,
        "assigned_screens": len(assigned_screens),
        "online": online,
        "offline": offline,
        "currently_playing": playing,
    }


@router.get("/campaigns/{campaign_id}/stats")
def get_campaign_stats(
    campaign_id: int,
    tenant: TenantScope = Depends(require_tenant_roles("owner", "editor", "viewer")),
    db: Session = Depends(get_db),
    _feature: TenantScope = Depends(require_feature("ads_counting")),
):
    from ..worker import aggregate_play_logs_sync
    aggregate_play_logs_sync(db)

    # Tenant scoping validation
    campaign = db.query(Campaign).filter(
        Campaign.organization_id == tenant.organization_id,
        Campaign.id == campaign_id
    ).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    week_start = today_start - timedelta(days=today_start.weekday())

    def get_period_stats(start_time, end_time=None):
        query = db.query(
            func.sum(PlayLogHourlyRollup.total_plays),
            func.sum(PlayLogHourlyRollup.completed_plays),
            func.sum(PlayLogHourlyRollup.error_plays)
        ).filter(
            PlayLogHourlyRollup.organization_id == tenant.organization_id,
            PlayLogHourlyRollup.campaign_id == campaign_id,
            PlayLogHourlyRollup.date_hour >= start_time
        )
        if end_time:
            query = query.filter(PlayLogHourlyRollup.date_hour < end_time)
        return query.first()

    today = get_period_stats(today_start)
    yesterday = get_period_stats(yesterday_start, today_start)
    week = get_period_stats(week_start)
    lifetime = db.query(
        func.sum(PlayLogHourlyRollup.total_plays),
        func.sum(PlayLogHourlyRollup.completed_plays),
        func.sum(PlayLogHourlyRollup.error_plays)
    ).filter(
        PlayLogHourlyRollup.organization_id == tenant.organization_id,
        PlayLogHourlyRollup.campaign_id == campaign_id
    ).first()

    def format_stats(row):
        total = row[0] or 0
        completed = row[1] or 0
        error = row[2] or 0
        success_pct = (completed / total * 100) if total > 0 else 0
        return {
            "total_plays": total,
            "success_percent": round(success_pct, 1)
        }

    return {
        "today": format_stats(today),
        "yesterday": format_stats(yesterday),
        "week": format_stats(week),
        "lifetime": format_stats(lifetime)
    }


@router.get("/campaigns/{campaign_id}/timeseries")
def get_campaign_timeseries(
    campaign_id: int,
    tenant: TenantScope = Depends(require_tenant_roles("owner", "editor", "viewer")),
    db: Session = Depends(get_db),
    _feature: TenantScope = Depends(require_feature("ads_counting")),
):
    from ..worker import aggregate_play_logs_sync
    aggregate_play_logs_sync(db)

    campaign = db.query(Campaign).filter(
        Campaign.organization_id == tenant.organization_id,
        Campaign.id == campaign_id
    ).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Last 7 days. Grouped by the hour column and bucketed to calendar days in Python --
    # see _bucket_daily.
    rows = db.query(
        PlayLogHourlyRollup.date_hour,
        func.sum(PlayLogHourlyRollup.total_plays),
        func.sum(PlayLogHourlyRollup.completed_plays),
    ).filter(
        PlayLogHourlyRollup.organization_id == tenant.organization_id,
        PlayLogHourlyRollup.campaign_id == campaign_id,
        PlayLogHourlyRollup.date_hour >= datetime.now(timezone.utc) - timedelta(days=7)
    ).group_by(PlayLogHourlyRollup.date_hour).all()

    return [
        {"date": day.isoformat(), "total_plays": total, "completed_plays": completed}
        for day, total, completed in _bucket_daily(rows)
    ]


@router.get("/campaigns/{campaign_id}/export")
def export_campaign_report(
    campaign_id: int,
    format: str = "csv",
    tenant: TenantScope = Depends(require_tenant_roles("owner", "editor", "viewer")),
    db: Session = Depends(get_db),
):
    # Every format exports the same play counts this route's siblings sell behind
    # ads_counting; PDF specifically is also sold on its own flag, same as report.pdf.
    ensure_feature(tenant, "ads_counting")
    if format == "pdf":
        ensure_feature(tenant, "pdf_reports")

    from ..worker import aggregate_play_logs_sync
    aggregate_play_logs_sync(db)

    campaign = db.query(Campaign).filter(
        Campaign.organization_id == tenant.organization_id,
        Campaign.id == campaign_id
    ).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    rows = db.query(
        PlayLogHourlyRollup.date_hour,
        Screen.name.label('screen_name'),
        func.sum(PlayLogHourlyRollup.total_plays).label('total_plays'),
        func.sum(PlayLogHourlyRollup.completed_plays).label('completed_plays'),
        func.sum(PlayLogHourlyRollup.error_plays).label('error_plays')
    ).join(
        Screen, Screen.id == PlayLogHourlyRollup.screen_id
    ).filter(
        PlayLogHourlyRollup.organization_id == tenant.organization_id,
        PlayLogHourlyRollup.campaign_id == campaign_id
    ).group_by(
        PlayLogHourlyRollup.date_hour, Screen.name
    ).order_by(PlayLogHourlyRollup.date_hour.desc()).all()

    filename = f"campaign_{campaign_id}_report.{format}"
    
    if format == "csv":
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(["Date/Hour", "Screen", "Total Plays", "Completed Plays", "Error Plays"])
        for row in rows:
            writer.writerow([row.date_hour.strftime("%Y-%m-%d %H:00"), row.screen_name, row.total_plays, row.completed_plays, row.error_plays])
        
        output.seek(0)
        return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})
    
    elif format == "excel":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Playback Report"
        ws.append(["Date/Hour", "Screen", "Total Plays", "Completed Plays", "Error Plays"])
        for row in rows:
            ws.append([row.date_hour.strftime("%Y-%m-%d %H:00"), row.screen_name, row.total_plays, row.completed_plays, row.error_plays])
        
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return StreamingResponse(iter([output.getvalue()]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename={filename}"})
    
    elif format == "pdf":
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
        
        output = BytesIO()
        doc = SimpleDocTemplate(output, pagesize=letter)
        
        data = [["Date/Hour", "Screen", "Total Plays", "Completed Plays", "Error Plays"]]
        for row in rows:
            data.append([row.date_hour.strftime("%Y-%m-%d %H:00"), row.screen_name, str(row.total_plays), str(row.completed_plays), str(row.error_plays)])
        
        table = Table(data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        
        doc.build([table])
        output.seek(0)
        return StreamingResponse(iter([output.getvalue()]), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={filename}"})
    
    else:
        raise HTTPException(status_code=400, detail="Invalid format")


@router.get("/media/{content_id}")
def get_media_report(
    content_id: int,
    tenant: TenantScope = Depends(require_tenant_roles("owner", "editor", "viewer")),
    db: Session = Depends(get_db),
    _feature: TenantScope = Depends(require_feature("ads_counting")),
):
    """Proof-of-play for one advert: where it runs, how often, and how reliably.

    Advertisers are billed on this, so every figure comes from PlayLogHourlyRollup — the
    deduplicated record the players upload — rather than from anything the dashboard
    infers about what *should* be playing.
    """
    from ..worker import aggregate_play_logs_sync
    aggregate_play_logs_sync(db)

    content = tenant.get(Content, content_id)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    target_org_id = content.organization_id

    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=day_start.weekday())
    month_start = day_start.replace(day=1)

    def totals(since: Optional[datetime] = None):
        query = db.query(
            func.coalesce(func.sum(PlayLogHourlyRollup.total_plays), 0),
            func.coalesce(func.sum(PlayLogHourlyRollup.completed_plays), 0),
            func.coalesce(func.sum(PlayLogHourlyRollup.error_plays), 0),
        ).filter(
            PlayLogHourlyRollup.organization_id == target_org_id,
            PlayLogHourlyRollup.media_id == content_id,
        )
        if since is not None:
            query = query.filter(PlayLogHourlyRollup.date_hour >= since)
        total, completed, errors = query.one()
        return {
            "total_plays": total,
            "completed_plays": completed,
            "error_plays": errors,
            "success_percent": round(completed / total * 100, 1) if total else 0.0,
        }

    # Every screen in the fleet, whether or not it has played this advert yet. An inner
    # join here silently dropped screens with no plays, so a screen that was scheduled but
    # had not started — exactly the one an operator is looking for — was simply absent
    # rather than showing zero.
    per_screen_rows = (
        db.query(
            Screen.id,
            Screen.name,
            Screen.location,
            Screen.latitude,
            Screen.longitude,
            ScreenGroup.name.label("group_name"),
            func.coalesce(func.sum(PlayLogHourlyRollup.total_plays), 0).label("plays"),
            func.coalesce(func.sum(PlayLogHourlyRollup.completed_plays), 0).label("completed"),
            func.coalesce(func.sum(PlayLogHourlyRollup.error_plays), 0).label("errors"),
            func.max(PlayLogHourlyRollup.date_hour).label("last_played"),
        )
        .outerjoin(
            PlayLogHourlyRollup,
            and_(
                PlayLogHourlyRollup.screen_id == Screen.id,
                PlayLogHourlyRollup.organization_id == target_org_id,
                PlayLogHourlyRollup.media_id == content_id,
            ),
        )
        .outerjoin(ScreenGroup, Screen.group_id == ScreenGroup.id)
        .filter(Screen.organization_id == target_org_id)
        .group_by(
            Screen.id, Screen.name, Screen.location,
            Screen.latitude, Screen.longitude, ScreenGroup.name,
        )
        .order_by(func.coalesce(func.sum(PlayLogHourlyRollup.total_plays), 0).desc())
        .all()
    )

    per_screen = [
        {
            "screen_id": row.id,
            "screen_name": row.name or f"Screen {row.id}",
            "group_name": row.group_name,
            "location": row.location,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "total_plays": row.plays,
            "completed_plays": row.completed,
            "error_plays": row.errors,
            "last_played": row.last_played.isoformat() if row.last_played else None,
        }
        for row in per_screen_rows
    ]

    places: dict[str, dict] = {}
    for row in per_screen:
        key = row["location"] or row["group_name"] or "No location set"
        place = places.setdefault(key, {"location": key, "screens": 0, "total_plays": 0, "completed_plays": 0})
        place["screens"] += 1
        place["total_plays"] += row["total_plays"]
        place["completed_plays"] += row["completed_plays"]

    daily_rows = (
        db.query(
            PlayLogHourlyRollup.date_hour,
            func.coalesce(func.sum(PlayLogHourlyRollup.total_plays), 0),
            func.coalesce(func.sum(PlayLogHourlyRollup.completed_plays), 0),
        )
        .filter(
            PlayLogHourlyRollup.organization_id == target_org_id,
            PlayLogHourlyRollup.media_id == content_id,
            PlayLogHourlyRollup.date_hour >= now - timedelta(days=30),
        )
        .group_by(PlayLogHourlyRollup.date_hour)
        .all()
    )

    return {
        "content_id": content_id,
        "today": totals(day_start),
        "week": totals(week_start),
        "month": totals(month_start),
        "lifetime": totals(),
        "per_screen": per_screen,
        "per_location": sorted(places.values(), key=lambda p: p["total_plays"], reverse=True),
        "daily": [
            {"date": day.isoformat(), "total_plays": total, "completed_plays": completed}
            for day, total, completed in _bucket_daily(daily_rows)
        ],
    }

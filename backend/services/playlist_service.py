"""Playlist Application Service containing business logic for playlist operations."""

from typing import List, Optional, Dict, Any
from fastapi import HTTPException
from sqlalchemy.orm import Session

from .base import BaseService
from ..repositories.playlist_repo import PlaylistRepository
from .. import models, schemas
from ..tenancy import TenantScope, ensure_feature
from ..routers.websockets import trigger_screen_sync


def set_schedule(item: models.PlaylistItem, payload: Optional[schemas.ScheduleBase]) -> None:
    """Helper to attach or update weekly schedule specification to a playlist item."""
    if payload is None:
        item.schedule = None
        return
    schedule = item.schedule or models.Schedule()
    schedule.days_of_week = ",".join(str(day) for day in payload.days_of_week) or None
    schedule.start_time = payload.start_time
    schedule.end_time = payload.end_time
    item.schedule = schedule


def bump_playlist(playlist: models.Playlist) -> None:
    """Update playlist timestamp to trigger freshness checks."""
    playlist.updated_at = models.utcnow()


class PlaylistService(BaseService):
    """Business service orchestrating playlist management and screen synchronization."""

    def __init__(self, db: Session, repo: Optional[PlaylistRepository] = None):
        super().__init__(db)
        self.repo = repo or PlaylistRepository(db)

    def create_playlist(self, payload: schemas.PlaylistCreate, scope: TenantScope) -> models.Playlist:
        """Create a new playlist under the tenant organization."""
        db_playlist = models.Playlist(
            organization_id=scope.organization_id,
            name=payload.name.strip(),
            default_transition=payload.default_transition,
            default_transition_ms=payload.default_transition_ms,
        )
        self.repo.add(db_playlist)
        self.commit()
        self.refresh(db_playlist)
        return db_playlist

    def list_playlists(self, scope: TenantScope) -> List[models.Playlist]:
        """List all playlists in tenant scope."""
        return self.repo.list_by_scope(scope)

    def get_playlist(self, playlist_id: int, scope: TenantScope) -> models.Playlist:
        """Get a single playlist by ID within tenant scope, raising 404 if not found."""
        playlist = self.repo.get_by_scope(scope, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")
        return playlist

    def update_playlist(
        self, playlist_id: int, payload: schemas.PlaylistUpdate, scope: TenantScope
    ) -> models.Playlist:
        """Update playlist properties and trigger screen sync."""
        playlist = scope.get(models.Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")

        fields = payload.model_fields_set
        if "name" in fields:
            if payload.name is None:
                raise HTTPException(status_code=422, detail="name cannot be null")
            playlist.name = payload.name.strip()
        if "default_transition" in fields:
            if payload.default_transition is None:
                raise HTTPException(status_code=422, detail="default_transition cannot be null")
            playlist.default_transition = payload.default_transition
        if "default_transition_ms" in fields:
            if payload.default_transition_ms is None:
                raise HTTPException(status_code=422, detail="default_transition_ms cannot be null")
            playlist.default_transition_ms = payload.default_transition_ms

        bump_playlist(playlist)
        self.commit()
        self.refresh(playlist)
        trigger_screen_sync(organization_id=scope.organization_id)
        return playlist

    def add_item_to_playlist(
        self, playlist_id: int, item: schemas.PlaylistItemCreate, scope: TenantScope
    ) -> models.PlaylistItem:
        """Add media content to a playlist, calculating duration and schedules."""
        db_playlist = scope.get(models.Playlist, playlist_id)
        if not db_playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")
        content = scope.get(models.Content, item.content_id)
        if not content:
            raise HTTPException(status_code=404, detail="Content not found")

        duration = item.duration
        if "duration" not in item.model_fields_set and content.duration_ms:
            duration = max(1, round(content.duration_ms / 1000))

        db_item = models.PlaylistItem(
            playlist_id=playlist_id,
            content_id=item.content_id,
            duration=duration,
            order=item.order,
            start_at=item.start_at,
            end_at=item.end_at,
            transition=item.transition,
            transition_ms=item.transition_ms,
        )
        if item.schedule is not None:
            set_schedule(db_item, item.schedule)

        bump_playlist(db_playlist)
        self.repo.add_item(db_item)
        self.commit()
        self.refresh(db_item)
        trigger_screen_sync(organization_id=scope.organization_id)
        return db_item

    def reorder_playlist_items(
        self, playlist_id: int, orders: List[int], scope: TenantScope
    ) -> Dict[str, Any]:
        """Reorder items within a playlist."""
        playlist = scope.get(models.Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")

        current_ids = {item.id for item in playlist.items}
        if len(orders) != len(set(orders)) or set(orders) != current_ids:
            raise HTTPException(
                status_code=422,
                detail="orders must contain every playlist item exactly once",
            )

        by_id = {item.id: item for item in playlist.items}
        for index, item_id in enumerate(orders):
            by_id[item_id].order = index

        bump_playlist(playlist)
        self.commit()
        trigger_screen_sync(organization_id=scope.organization_id)
        return {"status": "ok", "updated_at": playlist.updated_at}

    def update_playlist_item(
        self,
        playlist_id: int,
        item_id: int,
        payload: schemas.PlaylistItemUpdate,
        scope: TenantScope,
    ) -> models.PlaylistItem:
        """Update an existing playlist item's schedule, duration, or rotation."""
        if not scope.get(models.Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="Item not found")

        item = self.repo.get_item(playlist_id, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        fields = payload.model_fields_set
        if "duration" in fields:
            item.duration = payload.duration
        if item.content and item.content.type == "video" and item.content.duration_ms:
            item.duration = max(1, round(item.content.duration_ms / 1000))
        if "rotation" in fields:
            item.rotation = payload.rotation
        if "start_at" in fields:
            item.start_at = payload.start_at
        if "end_at" in fields:
            item.end_at = payload.end_at
        if item.start_at and item.end_at and item.end_at <= item.start_at:
            raise HTTPException(status_code=422, detail="end_at must be after start_at")
        if "schedule" in fields:
            set_schedule(item, payload.schedule)
        if "transition" in fields:
            # Gated on the value actually CHANGING, not merely being present and non-null: a
            # row already carrying a transition from before a downgrade (or an admin turning
            # the flag off) must still accept an unrelated resave of its duration, the same
            # way removing a feature never touches the data a tenant kept from when they had
            # it. Picking a genuinely different transition is the one thing this charges for,
            # matching update_playlist_transitions above.
            if payload.transition is not None and payload.transition != item.transition:
                ensure_feature(scope, "transitions")
            item.transition = payload.transition
        if "transition_ms" in fields:
            item.transition_ms = payload.transition_ms

        bump_playlist(item.playlist)
        self.commit()
        self.refresh(item)
        trigger_screen_sync(organization_id=scope.organization_id)
        return item

    def update_playlist_transitions(
        self,
        playlist_id: int,
        payload: schemas.PlaylistTransitionUpdate,
        scope: TenantScope,
    ) -> models.Playlist:
        """Update transitions across a playlist."""
        playlist = scope.get(models.Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")

        playlist.default_transition = payload.transition
        playlist.default_transition_ms = payload.transition_ms
        if payload.apply_to_all:
            for item in playlist.items:
                item.transition = payload.transition
                item.transition_ms = payload.transition_ms

        bump_playlist(playlist)
        self.commit()
        self.refresh(playlist)
        trigger_screen_sync(organization_id=scope.organization_id)
        return playlist

    def remove_item_from_playlist(
        self, playlist_id: int, item_id: int, scope: TenantScope
    ) -> Dict[str, str]:
        """Remove a single item from a playlist."""
        if not scope.get(models.Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="Item not found")

        item = self.repo.get_item(playlist_id, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        # An item a booking placed is removed by ending that booking's run on this screen,
        # not by deleting the row underneath it.
        #
        # AdPlacementTarget.playlist_item_id is ON DELETE SET NULL, and a target left with no
        # item is exactly what reconcile_unplaced_bookings exists to repair: it re-places it
        # within one supervisor tick. So the trash button on the screen page looked like it
        # worked and the advert came straight back. unplace_advert is the route the repair
        # code itself names for "stop this advert in one place" -- the target goes with the
        # item, the booking, its dates and its billing stay, and the advert keeps every other
        # screen it runs on. Adding it back while the booking runs is POST .../targets.
        from .placement_service import unplace_advert

        target = (
            self.db.query(models.AdPlacementTarget)
            .filter(models.AdPlacementTarget.playlist_item_id == item.id)
            .first()
        )
        playlist = item.playlist
        if target is not None:
            unplace_advert(scope, target)
        else:
            self.repo.delete_item(item)
            bump_playlist(playlist)
        self.commit()
        trigger_screen_sync(organization_id=scope.organization_id)
        return {"status": "ok"}

    def delete_playlist(self, playlist_id: int, scope: TenantScope) -> Dict[str, str]:
        """Unassign screens and groups, then delete playlist."""
        playlist = scope.get(models.Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")

        now = models.utcnow()
        for screen in playlist.screens:
            screen.playlist_id = None
            screen.assignment_updated_at = now
        for group in playlist.groups:
            group.playlist_id = None
            group.updated_at = now
            for screen in group.screens:
                screen.assignment_updated_at = now

        self.repo.delete(playlist)
        self.commit()
        trigger_screen_sync(organization_id=scope.organization_id)
        return {"status": "ok"}

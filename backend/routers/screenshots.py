import os
import uuid
import json
import logging
import datetime
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from .. import media_storage, models, schemas, database
from ..tenancy import TenantScope, get_tenant_scope, require_tenant_roles
from .content import UPLOAD_DIR, public_upload_url
from ..media_urls import is_s3_enabled, get_s3_config, resolve_media_url, storage_prefix, screen_screenshot_prefix
from .screens import verify_device_auth

# What a screen capture can be. The TV sends JPEG; PNG and WebP are accepted for other builds.
SCREENSHOT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

logger = logging.getLogger(__name__)
security = HTTPBearer()

router = APIRouter()

@router.post("/{screen_id}/request-screenshot")
async def request_screenshot(
    screen_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    screen = scope.get(models.Screen, screen_id)
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")

    try:
        redis = database.get_redis()
        await redis.publish(f"screen:{screen.device_id}", json.dumps({"type": "request_screenshot"}))
    except Exception as e:
        logger.warning(f"Failed to publish request_screenshot to redis: {e}")

    try:
        from .websockets import broadcast_in_memory
        if screen.device_id:
            await broadcast_in_memory(f"screen:{screen.device_id}", json.dumps({"type": "request_screenshot"}))
    except Exception:
        pass
        
    return {"status": "ok", "message": "Screenshot requested"}


@router.post("/device/{device_id}/screenshot")
def upload_device_screenshot(
    device_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(database.get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(security)
):
    screen = verify_device_auth(device_id, credentials, db)
    
    extension = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    # A screenshot is an image. With no allow-list the extension came straight from the
    # uploader, so a file named x.html was stored, and on local disk served from /uploads on
    # the API's own origin.
    if extension not in SCREENSHOT_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Screenshots must be JPEG, PNG or WebP images")
    stem = str(uuid.uuid4())
    unique_filename = f"{stem}{extension}"
    # Same tenant folder as the content library, under a screenshots/ root so retention
    # can sweep captures without touching uploaded media.
    prefix = screen_screenshot_prefix(screen.organization, screen.id)
    storage_key = f"{prefix}/{unique_filename}"
    
    file.file.seek(0)
    
    if is_s3_enabled():
        try:
            import boto3
            cfg = get_s3_config()
            s3 = boto3.client(
                "s3",
                endpoint_url=cfg["endpoint_url"],
                aws_access_key_id=cfg["aws_access_key_id"],
                aws_secret_access_key=cfg["aws_secret_access_key"],
                region_name=cfg["region_name"],
            )
            s3.upload_fileobj(
                file.file,
                cfg["bucket"],
                storage_key,
                ExtraArgs={"ContentType": media_storage.content_type_for(storage_key)},
            )
            file_url = f"s3://{storage_key}"
        except Exception as e:
            logger.error(f"Failed to upload screenshot to S3: {e}")
            raise HTTPException(status_code=500, detail="Storage error")
    else:
        local_path = os.path.join(UPLOAD_DIR, storage_key)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(file.file.read())
        # Both branches write the one `storage_key` built above. This branch used to
        # rebuild it as f"{prefix}/{unique_filename}", dropping the screenshots/ root that
        # the key exists to carry -- so on local disk every capture was filed into the
        # tenant's content folder, mixed in with their uploaded media, and the same
        # deployment moving to R2 silently changed where screenshots lived.
        file_url = public_upload_url(storage_key)

    screenshot = models.ScreenshotLog(
        organization_id=screen.organization_id,
        screen_id=screen.id,
        file_url=file_url
    )
    db.add(screenshot)
    db.commit()
    db.refresh(screenshot)
    
    return {"status": "ok", "url": resolve_media_url(file_url) or file_url}


@router.get("/{screen_id}/screenshots")
def list_screenshots(
    screen_id: int,
    limit: int = 10,
    scope: TenantScope = Depends(get_tenant_scope),
):
    screen = scope.get(models.Screen, screen_id)
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
        
    logs = (
        scope.query(models.ScreenshotLog)
        .filter(models.ScreenshotLog.screen_id == screen_id)
        .order_by(models.ScreenshotLog.created_at.desc())
        .limit(limit)
        .all()
    )
    
    return [
        {
            "id": log.id,
            "file_url": resolve_media_url(log.file_url) or log.file_url,
            "created_at": log.created_at
        }
        for log in logs
    ]

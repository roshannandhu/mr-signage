"""Stored-file references the orphan-cleanup scripts used to miss.

Content, renditions, screenshots and workspace logos were already treated as "in use". Three
more things point at stored files and would have been deleted as orphans:
  * the universal demo video (a SystemSetting row),
  * the APK of every TV app release (AppRelease.apk_url),
  * the hardcoded fallback demo reel served when that setting is unset (routers/screens.py).
"""
from backend import models

DEMO_VIDEO_KEY = "universal_demo_video_url"  # same value as routers/admin.py
# The default demo reel in routers/screens.py (used when the setting row is absent).
FALLBACK_DEMO_REEL = "/uploads/f9863204-f997-4122-ac1b-a50157e3d905.mp4"


def extra_stored_urls(db) -> list[str]:
    urls = [FALLBACK_DEMO_REEL]
    setting = db.query(models.SystemSetting).filter(models.SystemSetting.key == DEMO_VIDEO_KEY).first()
    if setting and setting.value:
        urls.append(setting.value)
    for organization in db.query(models.Organization).all():
        if organization.logo_url:
            urls.append(organization.logo_url)
    for release in db.query(models.AppRelease).all():
        if release.apk_url:
            urls.append(release.apk_url)
    return urls

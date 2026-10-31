"""Turn a shared Google Maps link into coordinates.

This exists so setting a screen's location needs no API key and no billing account. People
already share places as links — "share" in the Google Maps app produces exactly what this
parses — and every form of that link carries the coordinates in the URL itself.

Short maps.app.goo.gl / goo.gl/maps links do not, so those are resolved by following the
redirect, which is an ordinary HTTP request rather than a billed API call.
"""
from __future__ import annotations

import logging
import re
import json
import threading
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# Long-form links put the map centre after "@", e.g. /maps/place/Name/@9.9312,76.2673,17z
_AT = re.compile(r"@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)")
# "!3dLAT!4dLNG" is the *place* itself, which is more accurate than the map centre.
_BANG = re.compile(r"!3d(-?\d{1,3}\.\d+)!4d(-?\d{1,3}\.\d+)")
# ?q=LAT,LNG / ?query=LAT,LNG / ?ll=LAT,LNG / ?destination=LAT,LNG
_QUERY_KEYS = ("q", "query", "ll", "center", "destination", "daddr")
_PAIR = re.compile(r"^\s*(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*$")

# share.google is what the Android "Share" sheet now produces, and it is the form
# operators actually paste. It carries no coordinates until the redirect is followed.
SHORT_HOSTS = {"maps.app.goo.gl", "goo.gl", "maps.google.com", "g.co", "share.google"}
ACCEPTED_HOSTS = ("google.com", "goo.gl", "google.co.in", "share.google", "g.co")
TIMEOUT = 8


class MapsLinkError(ValueError):
    """The link could not be turned into a coordinate."""


def _valid(lat: float, lng: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lng <= 180


def _expand(url: str) -> str:
    """Follow redirects on a short link. Returns the original URL if it cannot."""
    request = urllib.request.Request(
        url,
        # Google serves a consent interstitial without a normal-looking client, and the
        # redirect we need never happens.
        headers={"User-Agent": "Mozilla/5.0 (compatible; MRSignage/1.0)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.geturl() or url
    except Exception as exc:
        logger.warning("could not expand maps link %s: %s", url, exc)
        return url


def _is_error_page(url: str) -> bool:
    """Whether a followed link landed on Google's own error page.

    A dead, expired or already-consumed share.google link is not answered with a 404.
    Google returns 200 and redirects to https://share.google/error, so the expansion
    looks like it worked and the result is simply a URL with no coordinate in it. The
    operator was then told to re-copy a link that will never resolve, however many
    times they try. Matched on the exact path so a place legitimately named "error"
    cannot trip it.
    """
    return urllib.parse.urlparse(url).path.rstrip("/").endswith("/error")


def _name_from(url: str) -> str | None:
    """The human place name Google puts in the path, e.g. /maps/place/Phoenix+Mall/."""
    match = re.search(r"/maps/place/([^/@]+)", url)
    if not match:
        return None
    name = urllib.parse.unquote_plus(match.group(1)).strip()
    # A name that is really just the coordinates is no better than the numbers.
    return None if not name or _PAIR.match(name) else name


def _name_from_search(url: str) -> str | None:
    """Place name out of a Google *search* URL.

    The Android share sheet produces share.google links, and those redirect to a search
    result rather than a map — there is no coordinate anywhere in them, only ?q=<name>.
    """
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    for value in query.get("q", []):
        name = value.strip()
        if name and not _PAIR.match(name):
            return name
    return None


# Nominatim publishes an absolute maximum of one request a second, enforced by blocking
# the calling IP. Setting a location is done once per screen, so a fleet rollout is a run
# of these back to back from one server address -- exactly the shape that trips it. A ban
# would take the feature out for every screen at once, so the interval is kept here
# rather than trusted to how fast an operator happens to paste.
NOMINATIM_MIN_INTERVAL = 1.0

# Screens in one building share a place name, and that name resolves to the same point
# every time. Successes only: caching a failure would let one timeout keep a location
# unresolvable until the process restarts.
_geocode_cache: dict[str, tuple[float, float]] = {}

# ponytail: one global lock serialises every geocode. Fine while this is an
# operator-driven action -- give it a per-process token bucket if it ever moves onto a
# bulk import path, where serialising would make the import as slow as the sum of its
# lookups.
_geocode_lock = threading.Lock()
_geocode_last_call = 0.0


def geocode(name: str) -> tuple[float, float] | None:
    """Coordinates for a place name, via OpenStreetMap's keyless geocoder.

    Used only when a link carries a name but no coordinate, which is what a share.google
    link expands to: a search result rather than a map.
    """
    key = name.strip().lower()
    if not key:
        return None
    cached = _geocode_cache.get(key)
    if cached is not None:
        return cached

    url = ("https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q="
           + urllib.parse.quote(name))
    request = urllib.request.Request(
        url, headers={"User-Agent": "MRSignage/1.0 (digital signage fleet management)"}
    )

    global _geocode_last_call
    try:
        with _geocode_lock:
            waited = NOMINATIM_MIN_INTERVAL - (time.monotonic() - _geocode_last_call)
            if waited > 0:
                time.sleep(waited)
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    results = json.loads(response.read())
            finally:
                # Stamped even when the call fails: a failed request still reached them,
                # and retrying it immediately is what the limit exists to prevent.
                _geocode_last_call = time.monotonic()
    except Exception as exc:
        logger.warning("geocode failed for %r: %s", name, exc)
        return None

    try:
        lat, lng = float(results[0]["lat"]), float(results[0]["lon"])
    except (IndexError, KeyError, TypeError, ValueError):
        # No match, or a reply in a shape this does not know. Either way it is "could not
        # locate", not a 500 -- this used to escape and the dialog showed nothing useful.
        return None
    if not _valid(lat, lng):
        return None

    _geocode_cache[key] = (lat, lng)
    return lat, lng


def parse(link: str) -> tuple[float, float, str | None]:
    """(latitude, longitude, place name or None) from any Google Maps URL.

    Raises MapsLinkError when the link carries no coordinate.
    """
    link = (link or "").strip()
    if not link:
        raise MapsLinkError("No link given.")
    if not link.startswith(("http://", "https://")):
        link = "https://" + link

    parsed = urllib.parse.urlparse(link)
    # `hostname`, not `netloc`: it lowercases, and drops any port and userinfo that could
    # otherwise be used to dress a foreign host up as a Google one.
    host = (parsed.hostname or "").lower()
    # Suffix matching alone accepted "nedgoogle.com", because it ends in "google.com" --
    # and _expand() then makes the server fetch whatever that host is, which is an SSRF
    # any operator could aim wherever they liked. The host must BE an accepted domain or
    # sit underneath one.
    if not any(host == accepted or host.endswith("." + accepted) for accepted in ACCEPTED_HOSTS):
        raise MapsLinkError("That is not a Google Maps link.")

    url = link
    # A short link has nothing to parse until it is followed.
    if host in SHORT_HOSTS and not _AT.search(url) and not _BANG.search(url):
        url = _expand(url)
        if _is_error_page(url):
            raise MapsLinkError(
                "Google could not open that link — it may have expired. Open the place "
                "in Google Maps and use Share → Copy link again."
            )

    # Most precise first: the place marker, then the map centre.
    for pattern in (_BANG, _AT):
        found = pattern.search(url)
        if found:
            lat, lng = float(found.group(1)), float(found.group(2))
            if _valid(lat, lng):
                return lat, lng, _name_from(url)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    for key in _QUERY_KEYS:
        for value in query.get(key, []):
            pair = _PAIR.match(value)
            if pair:
                lat, lng = float(pair.group(1)), float(pair.group(2))
                if _valid(lat, lng):
                    return lat, lng, _name_from(url)

    # No coordinate anywhere — but a share.google link still names the place, so resolve
    # the name instead of refusing a link the operator got from Google's own Share button.
    name = _name_from(url) or _name_from_search(url)
    if name:
        point = geocode(name)
        if point:
            return point[0], point[1], name

    raise MapsLinkError(
        "Could not locate that link. Open the place in Google Maps, then use "
        "Share → Copy link."
    )

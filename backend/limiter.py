import os

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

# Per-IP limiting for the endpoints that explicitly ask for it with @limiter.limit --
# the credential-guessing surfaces (login, password change, TV sign-in, device auth).
#
# `default_limits` is deliberately NOT relied on as a blanket cap: slowapi only applies it
# to every route if SlowAPIMiddleware is installed, which it is not, so nothing here rate
# limits an undecorated endpoint. That is the intended shape rather than an omission. A
# whole mall of screens sits behind one NAT public IP, so a global per-IP cap counts a
# 50-screen site as a single client and would take the site offline exactly when every TV
# reconnects at once -- a power cut, which is when it can least afford it. Add the
# middleware only alongside a key_func that identifies the device rather than the exit IP.
def _trusted_proxy_hops() -> int:
    """How many reverse proxies in front of this app append to X-Forwarded-For.

    0 (the default) keeps the historical behaviour: the leftmost entry is taken as the
    client. That entry is whatever the caller wrote, so it can be spoofed to dodge a
    per-IP cap. With N proxies in front of us, each appends the address it received the
    connection from, so the real client is the Nth entry FROM THE RIGHT -- everything to
    its left is client-supplied. Set TRUSTED_PROXY_HOPS only after checking, on the live
    host, what a request's X-Forwarded-For actually looks like: counting from the wrong end
    would put every caller in one bucket, which is the failure client_key exists to avoid.
    """
    try:
        return max(0, int(os.getenv("TRUSTED_PROXY_HOPS", "0")))
    except ValueError:
        return 0


def client_key(request: Request) -> str:
    """Who to count this request against.

    slowapi's get_remote_address returns request.client.host, which behind a reverse proxy
    -- Render, Fly, any managed host -- is the PROXY, identical for every caller on earth.
    Uvicorn only honours X-Forwarded-For from 127.0.0.1 unless told otherwise, so the
    header alone does not fix it. Left unhandled this turns every per-IP cap into a global
    one: a 5/minute login limit becomes five logins per minute for the whole product, and
    the device-auth cap becomes five TVs per minute for the entire fleet.

    Which entry to believe is set by TRUSTED_PROXY_HOPS (see _trusted_proxy_hops). Left at 0
    the leftmost entry is used: it is client-controlled, so a forger can spread their own
    attempts across buckets -- but counting everyone as one caller is the far worse failure.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        entries = [entry.strip() for entry in forwarded.split(",") if entry.strip()]
        if entries:
            hops = _trusted_proxy_hops()
            if hops > 0:
                return entries[max(0, len(entries) - hops)]
            return entries[0]
    return get_remote_address(request)


limiter = Limiter(key_func=client_key)

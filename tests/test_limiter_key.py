"""Which X-Forwarded-For entry the rate limiter counts a request against.

Pure: builds bare ASGI requests, touches no network and no database.

With no trusted proxies configured the leftmost entry is used, as it always was. With
TRUSTED_PROXY_HOPS=N the Nth entry from the RIGHT is used instead, because everything left of
it was written by the caller and can be rotated freely to dodge a per-IP cap (the 30/minute
login limit, for one).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from starlette.requests import Request  # noqa: E402

from backend.limiter import client_key  # noqa: E402


def request(forwarded=None, peer="10.0.0.9"):
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return Request({"type": "http", "headers": headers, "client": (peer, 5555), "method": "GET", "path": "/"})


@pytest.fixture(autouse=True)
def _hops(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)


def test_default_keeps_the_leftmost_entry():
    assert client_key(request("203.0.113.7, 10.1.1.1")) == "203.0.113.7"


def test_no_header_falls_back_to_the_socket_peer():
    assert client_key(request(None, peer="198.51.100.4")) == "198.51.100.4"


def test_one_trusted_proxy_means_the_rightmost_entry_is_the_client():
    os.environ["TRUSTED_PROXY_HOPS"] = "1"
    # The caller wrote "1.1.1.1, 2.2.2.2"; our proxy appended the address it really saw.
    assert client_key(request("1.1.1.1, 2.2.2.2, 203.0.113.7")) == "203.0.113.7"


def test_spoofing_the_left_of_the_header_no_longer_changes_the_bucket():
    os.environ["TRUSTED_PROXY_HOPS"] = "1"
    keys = {client_key(request(f"10.9.9.{n}, 203.0.113.7")) for n in range(50)}
    assert keys == {"203.0.113.7"}


def test_two_trusted_proxies_count_two_from_the_right():
    os.environ["TRUSTED_PROXY_HOPS"] = "2"
    # client -> proxy A -> proxy B -> app: A appended the client, B appended A.
    assert client_key(request("spoofed, 203.0.113.7, 10.2.2.2")) == "203.0.113.7"


def test_a_short_header_never_indexes_off_the_end():
    os.environ["TRUSTED_PROXY_HOPS"] = "3"
    assert client_key(request("203.0.113.7")) == "203.0.113.7"


def test_a_garbage_setting_is_treated_as_zero():
    os.environ["TRUSTED_PROXY_HOPS"] = "lots"
    assert client_key(request("203.0.113.7, 10.1.1.1")) == "203.0.113.7"

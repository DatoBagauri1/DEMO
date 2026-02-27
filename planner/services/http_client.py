from __future__ import annotations

import os

import httpx

DEFAULT_USER_AGENT = "TriPPlanner/1.0 (contact: https://trippilot.local)"


def trippilot_user_agent() -> str:
    value = (os.getenv("TRIPPILOT_USER_AGENT") or "").strip()
    return value or DEFAULT_USER_AGENT


def default_http_timeout() -> httpx.Timeout:
    connect = float(os.getenv("TRIPPILOT_HTTP_CONNECT_TIMEOUT", "5.0"))
    read = float(os.getenv("TRIPPILOT_HTTP_READ_TIMEOUT", "10.0"))
    write = float(os.getenv("TRIPPILOT_HTTP_WRITE_TIMEOUT", str(read)))
    pool = float(os.getenv("TRIPPILOT_HTTP_POOL_TIMEOUT", "5.0"))
    return httpx.Timeout(connect=connect, read=read, write=write, pool=pool)


def build_http_client(*, accept: str = "application/json") -> httpx.Client:
    headers = {
        "User-Agent": trippilot_user_agent(),
        "Accept": accept,
    }
    return httpx.Client(timeout=default_http_timeout(), headers=headers, follow_redirects=True)

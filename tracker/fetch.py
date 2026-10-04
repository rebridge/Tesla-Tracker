"""HTTP fetching with a browser-like TLS fingerprint when available.

tesla.com sits behind bot protection that rejects many plain HTTP clients.
curl_cffi impersonates a real Chrome TLS handshake, which gets through far more
often; urllib is the fallback so the package still imports without it.
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request

try:
    from curl_cffi import requests as cffi_requests
except ImportError:  # pragma: no cover - exercised only without the optional dep
    cffi_requests = None

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.tesla.com/inventory/new/my",
}


class FetchError(RuntimeError):
    pass


def fetch_text(url: str, *, retries: int = 2, timeout: int = 30) -> str:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return _fetch_once(url, timeout)
        except FetchError as exc:
            last = exc
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
    raise FetchError(f"{url[:120]}: {last}")


def _fetch_once(url: str, timeout: int) -> str:
    # Optional: route Tesla requests through your own proxy (e.g. a home machine),
    # since Tesla blocks cloud IP ranges such as GitHub-hosted runners.
    proxy = os.environ.get("TESLA_PROXY") or None
    if cffi_requests is not None:
        try:
            resp = cffi_requests.get(url, headers=HEADERS, timeout=timeout, impersonate="chrome", proxy=proxy)
        except Exception as exc:  # curl_cffi raises its own error hierarchy
            raise FetchError(str(exc)) from exc
        if resp.status_code != 200:
            raise FetchError(f"HTTP {resp.status_code}")
        return resp.text

    req = urllib.request.Request(url, headers=HEADERS)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else None)
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise FetchError(str(exc)) from exc

"""Diagnose access to tesla.com from wherever this runs: `python -m tracker.probe`.

Tries several request strategies and prints the HTTP status of each, so you can
see which (if any) gets past Tesla's bot protection from a given network.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from curl_cffi import requests

from .fetch import HEADERS
from .sources.inventory import build_url

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    config = tomllib.loads((ROOT / "config.toml").read_text())
    tracker, location = config["trackers"][0], config["location"]
    api = build_url({**tracker, "max_pages": 1}, location, 0)
    targets = {
        "api_v4": api,
        "api_v1": api.replace("/v4/", "/v1/"),
        "inventory_page": "https://www.tesla.com/inventory/new/my",
        "design_page": "https://www.tesla.com/modely/design",
        "home": "https://www.tesla.com/",
    }
    proxy = os.environ.get("TESLA_PROXY") or None
    print(f"proxy: {'TESLA_PROXY set' if proxy else 'none'}")
    for imp in ("chrome", "chrome131", "safari17_0", "firefox133", "edge101"):
        for name, url in targets.items():
            _try(f"{imp:11} plain   {name}", lambda: requests.get(url, headers=HEADERS, impersonate=imp, timeout=30, proxy=proxy))
        # Warm a session on an HTML page first so bot-manager cookies are set, then call the API.
        try:
            s = requests.Session(impersonate=imp, proxy=proxy)
            s.get("https://www.tesla.com/inventory/new/my", timeout=30)
            _try(f"{imp:11} session api_v4", lambda: s.get(api, headers={"Accept": "application/json", "Referer": targets["inventory_page"]}, timeout=30))
        except Exception as exc:
            print(f"{imp:11} session error: {exc}")


def _try(label: str, call) -> None:
    try:
        r = call()
        snippet = r.text[:120].replace("\n", " ")
        extra = ""
        if r.status_code == 200 and r.text.lstrip().startswith("{"):
            body = json.loads(r.text)
            extra = f" total_matches_found={body.get('total_matches_found')} results={len(body.get('results') or [])}"
        print(f"{label:40} {r.status_code} {len(r.text):>8}B{extra}  {snippet if r.status_code != 200 else ''}")
    except Exception as exc:
        print(f"{label:40} ERROR {exc}")


if __name__ == "__main__":
    main()

"""Tesla inventory API (the JSON behind tesla.com/inventory).

The API is undocumented, so field handling is deliberately defensive: every
price field we might need is read with fallbacks, and the raw price fields are
kept on each vehicle so a change in Tesla's schema is visible in the data.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from typing import Callable

from ..fetch import fetch_text

API_URL = "https://www.tesla.com/inventory/api/v4/inventory-results"
PAGE_SIZE = 50

# Price-like fields worth keeping verbatim for auditing.
RAW_PRICE_FIELDS = (
    "Price", "InventoryPrice", "PurchasePrice", "TotalPrice", "Discount",
    "DestinationHandlingFee", "OrderFee",
)


def build_url(tracker: dict, location: dict, offset: int) -> str:
    query = {
        "query": {
            "model": tracker["model"],
            "condition": tracker["condition"],
            "options": tracker.get("api_options", {}),
            "arrangeby": "Price",
            "order": "asc",
            "market": "US",
            "language": "en",
            "super_region": "north america",
            "lng": location["lng"],
            "lat": location["lat"],
            "zip": location["zip"],
            "range": 0,
            "region": location["region"],
        },
        "offset": offset,
        "count": PAGE_SIZE,
        "outsideOffset": 0,
        "outsideSearch": False,
        "isFalconDeliverySelectionEnabled": True,
        "version": "v2",
    }
    return f"{API_URL}?query={urllib.parse.quote(json.dumps(query, separators=(',', ':')))}"


def fetch_inventory(
    tracker: dict, location: dict, fetch: Callable[[str], str] = fetch_text, pause: float = 1.5
) -> list[dict]:
    """Return raw result dicts for every page (up to max_pages)."""
    results: list[dict] = []
    seen: set[str] = set()
    for page in range(int(tracker.get("max_pages", 20))):
        body = json.loads(fetch(build_url(tracker, location, page * PAGE_SIZE)))
        items = body.get("results")
        # With no exact matches the API returns an object instead of a list.
        if not isinstance(items, list) or not items:
            break
        for item in items:
            vin = item.get("VIN")
            if vin and vin not in seen:
                seen.add(vin)
                results.append(item)
        total = _to_number(body.get("total_matches_found"))
        if len(items) < PAGE_SIZE or (total is not None and len(seen) >= total):
            break
        if pause:
            time.sleep(pause)
    return results


def _to_number(value) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except ValueError:
        return None


def _join(value) -> str:
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value or "")


def detect_variant(item: dict) -> str:
    text = f"{item.get('TrimName', '')} {_join(item.get('DRIVE'))} {_join(item.get('TRIM'))}".upper()
    if "ALL-WHEEL" in text or "AWD" in text or "DUAL MOTOR" in text:
        return "AWD"
    return "RWD"


def matches(item: dict, tracker: dict) -> bool:
    if item.get("Model") and item["Model"] != tracker["model"]:
        return False
    name = str(item.get("TrimName", "")).lower()
    include = [t.lower() for t in tracker.get("trim_include", [])]
    exclude = [t.lower() for t in tracker.get("trim_exclude", [])]
    if include and not any(t in name for t in include):
        return False
    if any(t in name for t in exclude):
        return False
    odo = _to_number(item.get("Odometer"))
    if tracker.get("max_odometer") is not None and odo is not None and odo > tracker["max_odometer"]:
        return False
    year = _to_number(item.get("Year"))
    if tracker.get("min_year") is not None and year is not None and year < tracker["min_year"]:
        return False
    return True


def normalize(item: dict, tracker: dict) -> dict | None:
    """Reduce a raw inventory record to the fields the deal math needs."""
    list_price = _first_number(item, "Price", "InventoryPrice", "TotalPrice")
    discount = _to_number(item.get("Discount")) or 0.0
    purchase = _to_number(item.get("PurchasePrice"))
    if purchase is not None and list_price is not None and purchase < list_price:
        # PurchasePrice already reflects any discount; trust it over our own arithmetic.
        discount = max(discount, list_price - purchase)
    if list_price is None:
        list_price = purchase
    if list_price is None:
        return None
    vin = item.get("VIN", "")
    condition = tracker["condition"]
    return {
        "vin": vin,
        "year": item.get("Year"),
        "trim": item.get("TrimName", ""),
        "variant": detect_variant(item),
        "list_price": round(list_price),
        "discount": round(discount),
        "price": round(list_price - discount),
        "destination_fee": _to_number(item.get("DestinationHandlingFee")),
        "order_fee": _to_number(item.get("OrderFee")),
        "paint": _join(item.get("PAINT")),
        "wheels": _join(item.get("WHEELS")),
        "interior": _join(item.get("INTERIOR")),
        "odometer": _to_number(item.get("Odometer")),
        "is_demo": bool(item.get("IsDemo")),
        "location": ", ".join(p for p in (item.get("City"), item.get("StateProvince")) if p),
        "url": f"https://www.tesla.com/{tracker['model']}/order/{vin}"
        if condition == "new"
        else f"https://www.tesla.com/used/{vin}",
        "raw_prices": {k: item[k] for k in RAW_PRICE_FIELDS if k in item},
    }


def _first_number(item: dict, *keys: str) -> float | None:
    for key in keys:
        value = _to_number(item.get(key))
        if value:
            return value
    return None


def collect(
    tracker: dict, location: dict, fetch: Callable[[str], str] = fetch_text
) -> tuple[list[dict], dict[str, int]]:
    """Return (matching normalized vehicles, count of every TrimName seen)."""
    raw = fetch_inventory(tracker, location, fetch)
    vehicles, trims = [], {}
    for item in raw:
        name = str(item.get("TrimName", "?"))
        trims[name] = trims.get(name, 0) + 1
        if matches(item, tracker):
            v = normalize(item, tracker)
            if v is not None:
                vehicles.append(v)
    return vehicles, trims

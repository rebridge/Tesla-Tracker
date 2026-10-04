"""Turn inventory + incentives into one comparable offer per variant."""

from __future__ import annotations

import datetime as dt
import tomllib
from pathlib import Path


def load_manual_offers(path: Path, tracker_id: str, today: dt.date) -> list[dict]:
    if not path.exists():
        return []
    entries = tomllib.loads(path.read_text()).get("offers", [])
    active = []
    for entry in entries:
        if entry.get("tracker") != tracker_id:
            continue
        start = _date(entry.get("start"))
        end = _date(entry.get("end"))
        if (start and today < start) or (end and today > end):
            continue
        active.append(entry)
    return active


def _date(value) -> dt.date | None:
    if value is None or isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def monthly_payment(principal: float, apr: float, months: int) -> float:
    if principal <= 0:
        return 0.0
    r = apr / 100 / 12
    if r == 0:
        return principal / months
    return principal * r / (1 - (1 + r) ** -months)


def choose_apr(variant: str, manual: list[dict], auto: dict[str, dict], fallback: float) -> dict:
    manual_aprs = [o for o in manual if o.get("kind") == "apr" and variant in o.get("variants", [variant])]
    if manual_aprs:
        best = min(manual_aprs, key=lambda o: o["rate"])
        return {
            "rate": float(best["rate"]),
            "max_term_months": best.get("max_term_months"),
            "source": "manual",
            "label": best.get("label", f"{best['rate']}% APR"),
            "evidence": best.get("source", ""),
        }
    if variant in auto:
        a = auto[variant]
        return {
            "rate": a["rate"],
            "max_term_months": a.get("max_term_months"),
            "source": "tesla.com",
            "label": f"{a['rate']}% APR" + (f" up to {a['max_term_months']} mo" if a.get("max_term_months") else ""),
            "evidence": a.get("evidence", ""),
        }
    return {
        "rate": float(fallback),
        "max_term_months": None,
        "source": "fallback",
        "label": f"{fallback}% APR (standard rate; no promo found)",
        "evidence": "",
    }


def build_offer(
    variant: str,
    vehicles: list[dict],
    manual: list[dict],
    auto_apr: dict[str, dict],
    config: dict,
) -> dict:
    pool = sorted((v for v in vehicles if v["variant"] == variant), key=lambda v: v["price"])
    offer: dict = {"variant": variant, "available": bool(pool), "inventory_count": len(pool)}
    if not pool:
        return offer

    fees_cfg = config.get("fees", {})
    fin = config["financing"]
    best = pool[0]
    destination = best["destination_fee"] if best.get("destination_fee") is not None else fees_cfg.get("destination", 0)
    order_fee = best["order_fee"] if best.get("order_fee") is not None else fees_cfg.get("order_fee", 0)

    applies = [o for o in manual if variant in o.get("variants", [variant])]
    cash_incentives = [o for o in applies if o.get("kind") == "cash"]
    perks = [o for o in applies if o.get("kind") == "perk"]
    cash_off = sum(float(o["amount"]) for o in cash_incentives)
    perk_value = sum(float(o["value"]) for o in perks)

    apr = choose_apr(variant, manual, auto_apr, fin["fallback_apr"])
    term = int(fin["term_months"])
    if apr["max_term_months"]:
        term = min(term, int(apr["max_term_months"]))

    cash_price = best["price"] + destination + order_fee - cash_off
    down = min(float(fin["down_payment"]), cash_price)
    financed = cash_price - down
    payment = monthly_payment(financed, apr["rate"], term)
    total_paid = down + payment * term

    offer.update(
        {
            "vehicle": {k: best.get(k) for k in ("vin", "year", "trim", "paint", "wheels", "interior", "location", "url", "is_demo", "odometer")},
            "list_price": best["list_price"],
            "inventory_discount": best["discount"],
            "vehicle_price": best["price"],
            "destination_fee": round(destination),
            "order_fee": round(order_fee),
            "cash_incentives": [{"label": o.get("label", "Cash incentive"), "amount": float(o["amount"])} for o in cash_incentives],
            "perks": [{"label": o.get("label", "Perk"), "value": float(o["value"])} for o in perks],
            "apr": apr,
            "term_months": term,
            "down_payment": round(down),
            "amount_financed": round(financed),
            "monthly_payment": round(payment, 2),
            "total_interest": round(payment * term - financed),
            "cash_price": round(cash_price),
            "metrics": {
                "cash_price": round(cash_price - perk_value),
                "financed_total": round(total_paid - perk_value),
            },
        }
    )
    return offer

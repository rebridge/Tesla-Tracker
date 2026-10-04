"""Decide whether a deal meaningfully improved, and describe what changed."""

from __future__ import annotations


def usd(value: float, signed: bool = False) -> str:
    sign = ""
    if signed:
        sign = "+" if value > 0 else "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def evaluate(variant_state: dict | None, offer: dict, metric: str, min_savings: float) -> dict:
    """Compare an offer to the stored state for its variant.

    The baseline is the deal at the last alert. Small improvements accumulate
    against it until they cross the threshold; if the deal gets worse, the
    baseline moves up with it, so a later recovery counts as an improvement.
    Returns the alert decision plus the new state to store.
    """
    result = {"alert": False, "savings_vs_baseline": 0.0, "savings_vs_previous": None, "state": variant_state}
    if not offer.get("available"):
        return result

    value = offer["metrics"][metric]
    if not variant_state or "baseline" not in variant_state:
        # First observation establishes the baseline; nothing to compare yet.
        result["state"] = {"baseline": offer, "previous": offer}
        return result

    baseline = variant_state["baseline"]
    previous = variant_state.get("previous") or baseline
    base_value = baseline["metrics"][metric]
    result["savings_vs_baseline"] = base_value - value
    if previous.get("available"):
        result["savings_vs_previous"] = previous["metrics"][metric] - value

    if base_value - value >= min_savings:
        result["alert"] = True
        result["prior_offer"] = baseline
        new_baseline = offer
    elif value > base_value:
        new_baseline = offer
    else:
        new_baseline = baseline
    result["state"] = {"baseline": new_baseline, "previous": offer}
    return result


def describe_changes(prior: dict, current: dict) -> list[str]:
    """Human-readable list of what differs between two offers for one variant."""
    lines: list[str] = []
    if prior.get("list_price") != current.get("list_price"):
        lines.append(
            f"List price {usd(prior['list_price'])} → {usd(current['list_price'])} "
            f"({usd(current['list_price'] - prior['list_price'], signed=True)})"
        )
    if prior.get("inventory_discount") != current.get("inventory_discount"):
        lines.append(
            f"Tesla inventory discount {usd(prior['inventory_discount'])} → {usd(current['inventory_discount'])}"
        )
    if prior.get("vehicle_price") != current.get("vehicle_price") and not lines:
        lines.append(f"Vehicle price {usd(prior['vehicle_price'])} → {usd(current['vehicle_price'])}")
    for fee in ("destination_fee", "order_fee"):
        if prior.get(fee) != current.get(fee):
            lines.append(f"{fee.replace('_', ' ').capitalize()} {usd(prior[fee])} → {usd(current[fee])}")

    pa, ca = prior.get("apr", {}), current.get("apr", {})
    if pa.get("rate") != ca.get("rate"):
        lines.append(f"APR {pa.get('rate')}% → {ca.get('rate')}% ({ca.get('label')}; source: {ca.get('source')})")
    if prior.get("term_months") != current.get("term_months"):
        lines.append(f"Financing term {prior.get('term_months')} → {current.get('term_months')} months")

    def labels(offer: dict, key: str) -> dict[str, float]:
        return {i["label"]: i.get("amount", i.get("value", 0)) for i in offer.get(key, [])}

    for key, noun in (("cash_incentives", "Cash incentive"), ("perks", "Perk")):
        before, after = labels(prior, key), labels(current, key)
        for label in after.keys() - before.keys():
            lines.append(f"New {noun.lower()}: {label} ({usd(after[label])})")
        for label in before.keys() - after.keys():
            lines.append(f"{noun} ended: {label} ({usd(before[label])})")

    if (prior.get("vehicle") or {}).get("vin") != (current.get("vehicle") or {}).get("vin"):
        lines.append("Cheapest matching vehicle changed (different VIN)")
    return lines

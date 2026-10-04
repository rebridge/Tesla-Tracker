import datetime as dt
import json
import tomllib
from pathlib import Path

import pytest

from tracker import compare, deal, main
from tracker.sources import apr, inventory

ROOT = Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text())
TRACKER = CONFIG["trackers"][0]

DESIGN_PAGE = """<html><script>window.tesla={"copy":"1.99\\u0025 APR available for Model Y Premium
Rear-Wheel Drive and Premium All-Wheel Drive for up to 72 months with 10\\u0025 down.
3.99\\u0025 APR for Model Y Performance for up to 72 months. 0.99\\u0025 APR for Model 3 Premium."}</script></html>"""


def car(vin, trim, price, discount=0, **extra):
    return {
        "VIN": vin, "Model": "my", "TrimName": trim, "Year": 2026, "Price": price,
        "PurchasePrice": price - discount, "Discount": discount, "PAINT": ["WHITE"],
        "WHEELS": ["NINETEEN"], "INTERIOR": ["PREMIUM_BLACK"], "City": "Raleigh",
        "StateProvince": "NC", **extra,
    }


def inventory_body(awd_price=48990, rwd_price=44990, awd_discount=0):
    return {
        "total_matches_found": "4",
        "results": [
            car("STD1", "Model Y Standard Rear-Wheel Drive", 39990),
            car("RWD1", "Model Y Premium Rear-Wheel Drive", rwd_price),
            car("AWD1", "Model Y Premium All-Wheel Drive", awd_price, awd_discount),
            car("PERF1", "Model Y Performance All-Wheel Drive", 57490),
        ],
    }


def make_fetch(body, page=DESIGN_PAGE):
    def fetch(url):
        if "inventory-results" in url:
            return json.dumps(body)
        return page
    return fetch


def test_matches_and_variants():
    vehicles, trims = inventory.collect(TRACKER, CONFIG["location"], make_fetch(inventory_body()))
    assert sorted((v["vin"], v["variant"]) for v in vehicles) == [("AWD1", "AWD"), ("RWD1", "RWD")]
    assert trims["Model Y Performance All-Wheel Drive"] == 1


def test_normalize_uses_purchase_price_discount():
    v = inventory.normalize(car("X", "Model Y Premium All-Wheel Drive", 48990, 1000), TRACKER)
    assert (v["list_price"], v["discount"], v["price"]) == (48990, 1000, 47990)


def test_apr_extraction_scoped_to_trim_and_model():
    found = apr.extract_aprs(apr.page_text(DESIGN_PAGE), TRACKER)
    assert found["RWD"]["rate"] == 1.99 and found["AWD"]["rate"] == 1.99
    assert found["AWD"]["max_term_months"] == 72


def test_apr_ignores_other_trims():
    found = apr.extract_aprs("3.99% APR for Model Y Performance for 72 months.", TRACKER)
    assert found == {}


def test_monthly_payment():
    assert deal.monthly_payment(50000, 6, 60) == pytest.approx(966.64, abs=0.01)
    assert deal.monthly_payment(36000, 0, 72) == 500


def test_manual_offer_window(tmp_path):
    p = tmp_path / "offers.toml"
    p.write_text(
        '[[offers]]\ntracker="my-premium-new"\nkind="cash"\namount=1000\nlabel="x"\nend="2026-10-31"\n'
    )
    assert deal.load_manual_offers(p, "my-premium-new", dt.date(2026, 10, 4))
    assert not deal.load_manual_offers(p, "my-premium-new", dt.date(2026, 11, 1))


def test_baseline_accumulates_and_resets():
    def offer(total):
        return {"available": True, "metrics": {"financed_total": total, "cash_price": total}}

    r = compare.evaluate(None, offer(60000), "financed_total", 500)
    assert not r["alert"]
    r = compare.evaluate(r["state"], offer(59700), "financed_total", 500)  # -300: below threshold
    assert not r["alert"]
    r = compare.evaluate(r["state"], offer(59400), "financed_total", 500)  # -600 vs baseline
    assert r["alert"] and r["savings_vs_baseline"] == 600
    r = compare.evaluate(r["state"], offer(61000), "financed_total", 500)  # worse: baseline moves up
    assert not r["alert"] and r["state"]["baseline"]["metrics"]["financed_total"] == 61000
    r = compare.evaluate(r["state"], offer(60400), "financed_total", 500)
    assert r["alert"]


def run_once(tmp_path, body, page=DESIGN_PAGE, day=4, offers="offers.toml"):
    now = dt.datetime(2026, 10, day, 12, tzinfo=dt.timezone.utc)
    offers_path = offers if isinstance(offers, Path) else ROOT / offers
    return main.run(tmp_path, CONFIG, offers_path, now=now, fetch=make_fetch(body, page))


def test_end_to_end_alert_on_price_drop(tmp_path, capsys):
    first = run_once(tmp_path, inventory_body())
    assert first["alerts"] == []
    second = run_once(tmp_path, inventory_body(awd_discount=1500), day=5)
    assert [a["variant"] for a in second["alerts"]] == ["AWD"]
    alert = second["alerts"][0]
    assert alert["savings"] > 1500  # price drop plus interest saved on the smaller loan
    assert any("discount" in c for c in alert["changes"])
    out = capsys.readouterr().out
    assert "Estimated savings vs prior offer" in out and "1.99% APR" in out
    history = json.loads((tmp_path / "history" / "my-premium-new.json").read_text())
    assert len(history) == 2


def test_apr_drop_alerts_with_financing_terms(tmp_path, capsys):
    run_once(tmp_path, inventory_body(), page=DESIGN_PAGE.replace("1.99", "3.99"))
    result = run_once(tmp_path, inventory_body(), day=5)
    assert {a["variant"] for a in result["alerts"]} == {"RWD", "AWD"}
    assert any("APR 3.99% → 1.99%" in c for c in result["alerts"][0]["changes"])
    assert "Financing terms" in capsys.readouterr().out


def test_fetch_failure_counts_toward_health(tmp_path):
    def broken(url):
        raise RuntimeError("HTTP 403")

    now = dt.datetime(2026, 10, 4, tzinfo=dt.timezone.utc)
    summary = main.run(tmp_path, CONFIG, ROOT / "offers.toml", now=now, fetch=broken)
    assert "error" in summary["trackers"]["my-premium-new"]
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["trackers"]["my-premium-new"]["health"]["failures"] == 1


def test_no_match_warns_with_trims_seen(tmp_path):
    body = {"total_matches_found": "1", "results": [car("A", "Model Y Long Range AWD", 50000)]}
    summary = run_once(tmp_path, body)
    assert "Model Y Long Range AWD" in summary["trackers"]["my-premium-new"]["warnings"][0]

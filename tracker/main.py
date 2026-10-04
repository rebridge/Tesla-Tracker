"""Entry point: `python -m tracker run` (check + alert) and `python -m tracker site` (build Pages)."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import tomllib
import traceback
from pathlib import Path
from typing import Callable

from . import compare, deal, notify
from .fetch import fetch_text
from .sources import apr, inventory

ROOT = Path(__file__).resolve().parent.parent
VEHICLES_KEPT_PER_VARIANT = 25


def load_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def history_point(offers: dict[str, dict], date: str) -> dict:
    point = {"date": date, "variants": {}}
    for variant, o in offers.items():
        if not o.get("available"):
            point["variants"][variant] = {"available": False}
            continue
        point["variants"][variant] = {
            "available": True,
            "vehicle_price": o["vehicle_price"],
            "apr": o["apr"]["rate"],
            "term_months": o["term_months"],
            "monthly_payment": o["monthly_payment"],
            "cash_price": o["metrics"]["cash_price"],
            "financed_total": o["metrics"]["financed_total"],
            "inventory_count": o["inventory_count"],
        }
    return point


def append_history(history: list[dict], point: dict) -> bool:
    """Append when the offer changed, or once per day as a heartbeat."""
    if history:
        last = history[-1]
        same_offer = last["variants"] == point["variants"]
        if same_offer and last["date"][:10] == point["date"][:10]:
            return False
    history.append(point)
    return True


def run(
    data_dir: Path,
    config: dict,
    offers_path: Path,
    *,
    now: dt.datetime | None = None,
    fetch: Callable[[str], str] = fetch_text,
    github: notify.GitHub | None = None,
    site_url: str | None = None,
) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    state = load_json(data_dir / "state.json", {"trackers": {}})
    alerts_log = load_json(data_dir / "alerts.json", [])
    alert_cfg = config["alerts"]
    metric = alert_cfg.get("metric", "financed_total")
    summary = {"checked_at": stamp, "trackers": {}, "alerts": []}

    for tracker in config["trackers"]:
        tid = tracker["id"]
        tstate = state["trackers"].setdefault(tid, {"variants": {}, "health": {"failures": 0}})
        health = tstate["health"]
        tsum = summary["trackers"][tid] = {"name": tracker["name"], "warnings": []}

        try:
            vehicles, trims_seen = inventory.collect(tracker, config["location"], fetch)
        except Exception as exc:  # network, bot wall, schema change
            health["failures"] = health.get("failures", 0) + 1
            health["last_error"] = f"{stamp} inventory: {exc}"
            tsum["error"] = str(exc)
            print(f"[{tid}] inventory fetch failed: {exc}", file=sys.stderr)
            _maybe_open_health_issue(github, tracker, health, alert_cfg)
            continue

        auto_apr: dict = {}
        try:
            auto_apr = apr.collect(tracker, fetch)
        except Exception as exc:
            tsum["warnings"].append(f"APR detection failed: {exc}")

        if not vehicles:
            tsum["warnings"].append(
                "No vehicles matched the trim filter. Trims seen: "
                + (", ".join(f"{k} ({v})" for k, v in sorted(trims_seen.items())) or "none")
            )

        manual = deal.load_manual_offers(offers_path, tid, now.date())
        offers = {
            variant: deal.build_offer(variant, vehicles, manual, auto_apr, config)
            for variant in tracker.get("variants", ["RWD", "AWD"])
        }

        for variant, offer in offers.items():
            result = compare.evaluate(
                tstate["variants"].get(variant), offer, metric, float(alert_cfg["min_savings_usd"])
            )
            if result["state"] is not None:
                tstate["variants"][variant] = result["state"]
            if not result["alert"]:
                continue
            title = notify.alert_title(tracker, variant, result["savings_vs_baseline"], metric)
            body = notify.alert_body(tracker, result["prior_offer"], offer, metric, site_url)
            url = None
            if github:
                github.ensure_label(notify.ALERT_LABEL, "1f883d", "Tesla deal improved")
                url = github.create_issue(title, body, notify.ALERT_LABEL)
            else:
                print(f"\n=== {title} ===\n{body}\n")
            entry = {
                "at": stamp, "tracker": tid, "variant": variant, "title": title, "url": url,
                "savings": round(result["savings_vs_baseline"]), "metric": metric,
                "changes": compare.describe_changes(result["prior_offer"], offer),
            }
            alerts_log.append(entry)
            summary["alerts"].append(entry)

        history_path = data_dir / "history" / f"{tid}.json"
        history = load_json(history_path, [])
        if append_history(history, history_point(offers, stamp)):
            save_json(history_path, history)

        kept = sorted(vehicles, key=lambda v: v["price"])
        by_variant: dict[str, list] = {}
        for v in kept:
            bucket = by_variant.setdefault(v["variant"], [])
            if len(bucket) < VEHICLES_KEPT_PER_VARIANT:
                bucket.append(v)
        save_json(data_dir / "latest" / f"{tid}.json", {
            "tracker": tracker,
            "offers": offers,
            "auto_apr": auto_apr,
            "manual_offers": [{k: str(v) if isinstance(v, dt.date) else v for k, v in o.items()} for o in manual],
            "vehicles": by_variant,
            "trims_seen": trims_seen,
            "warnings": tsum["warnings"],
        })

        if health.get("failures", 0) and github:
            _close_health_issues(github, tracker)
        health["failures"] = 0
        health["last_success"] = stamp
        tsum["offers"] = {
            v: (o["metrics"] if o.get("available") else None) for v, o in offers.items()
        }

    save_json(data_dir / "state.json", state)
    save_json(data_dir / "alerts.json", alerts_log)
    return summary


def _maybe_open_health_issue(github, tracker: dict, health: dict, alert_cfg: dict) -> None:
    if not github or health["failures"] < int(alert_cfg.get("health_failure_threshold", 3)):
        return
    if any(tracker["id"] in i["title"] for i in github.open_issues(notify.HEALTH_LABEL)):
        return
    github.ensure_label(notify.HEALTH_LABEL, "d1242f", "The monitor cannot read Tesla's data")
    github.create_issue(
        f"Monitor failing: {tracker['name']} ({tracker['id']})",
        f"The last {health['failures']} runs could not read Tesla inventory.\n\n"
        f"Latest error: `{health.get('last_error')}`\n\n"
        "Common causes: Tesla blocking the runner's network (GitHub-hosted runners get HTTP 403), or a "
        "change in the inventory API. See the README section \"Running where Tesla allows it\", and the "
        "*Diagnose Tesla access* workflow. This issue closes itself when a run succeeds.",
        notify.HEALTH_LABEL,
    )


def _close_health_issues(github, tracker: dict) -> None:
    for issue in github.open_issues(notify.HEALTH_LABEL):
        if tracker["id"] in issue["title"]:
            github.comment(issue["number"], "Recovered: the latest run read Tesla inventory successfully.")
            github.close(issue["number"])


def build_site(data_dir: Path, out_dir: Path, config: dict, run_info: dict) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(ROOT / "web", out_dir)
    shutil.copytree(data_dir, out_dir / "data", dirs_exist_ok=True)
    save_json(out_dir / "data" / "run.json", run_info)
    save_json(out_dir / "data" / "index.json", {
        "trackers": [{"id": t["id"], "name": t["name"]} for t in config["trackers"]],
        "alerts": config["alerts"],
        "financing": config["financing"],
    })


def write_step_summary(summary: dict) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"### Tesla deal check — {summary['checked_at']}", ""]
    for tid, t in summary["trackers"].items():
        lines.append(f"**{t['name']}**: " + ("FAILED — " + t["error"] if "error" in t else "ok"))
        for variant, m in (t.get("offers") or {}).items():
            lines.append(
                f"- {variant}: " + (f"cash ${m['cash_price']:,} · financed ${m['financed_total']:,}" if m else "none in inventory")
            )
        lines += [f"- ⚠️ {w}" for w in t["warnings"]]
    lines += ["", f"Alerts: {len(summary['alerts'])}"]
    with open(path, "a") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracker")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="check Tesla and alert on improvements")
    r.add_argument("--dry-run", action="store_true", help="print alerts instead of opening issues")
    s = sub.add_parser("site", help="build the GitHub Pages site into ./site")
    s.add_argument("--out", default="site")
    args = parser.parse_args(argv)

    config = tomllib.loads((ROOT / "config.toml").read_text())
    data_dir = ROOT / "data"
    repo = os.environ.get("GITHUB_REPOSITORY")
    site_url = os.environ.get("SITE_URL") or (
        f"https://{repo.split('/')[0].lower()}.github.io/{repo.split('/')[1]}/" if repo else None
    )

    if args.cmd == "site":
        run_info = load_json(data_dir / "last_run.json", {})
        if os.environ.get("GITHUB_RUN_ID") and repo:
            run_info["run_url"] = f"https://github.com/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        build_site(data_dir, ROOT / args.out, config, run_info)
        return 0

    github = None if args.dry_run else notify.GitHub.from_env()
    try:
        summary = run(data_dir, config, ROOT / "offers.toml", github=github, site_url=site_url)
    except Exception:
        traceback.print_exc()
        return 1
    # Not committed (gitignored): lets the site show when the last check ran.
    save_json(data_dir / "last_run.json", {"checked_at": summary["checked_at"], "trackers": summary["trackers"]})
    write_step_summary(summary)
    print(json.dumps(summary, indent=2))
    # Fetch failures are reported through the health issue, not a red workflow run,
    # so a multi-hour Tesla outage doesn't send a failure email every run.
    return 0

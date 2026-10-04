"""Alert text and delivery (GitHub issues, which GitHub also emails to you)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .compare import describe_changes, usd

ALERT_LABEL = "deal-alert"
HEALTH_LABEL = "monitor-health"


def _row(label: str, prior, now) -> str:
    return f"| {label} | {prior} | {now} |"


def _apr_text(offer: dict) -> str:
    return f"{offer['apr']['rate']}% / {offer['term_months']} mo"


def alert_title(tracker: dict, variant: str, savings: float, metric: str) -> str:
    what = "financed total" if metric == "financed_total" else "cash price"
    return f"Deal alert: {tracker['name']} {variant} improved {usd(savings)} ({what})"


def alert_body(tracker: dict, prior: dict, current: dict, metric: str, site_url: str | None) -> str:
    fin_save = prior["metrics"]["financed_total"] - current["metrics"]["financed_total"]
    cash_save = prior["metrics"]["cash_price"] - current["metrics"]["cash_price"]
    pay_delta = current["monthly_payment"] - prior["monthly_payment"]
    v = current["vehicle"]
    changes = describe_changes(prior, current) or ["No itemized change found (rounding or fee data shifted)."]
    apr_changed = prior["apr"]["rate"] != current["apr"]["rate"] or prior["term_months"] != current["term_months"]

    lines = [
        f"## {tracker['name']} — {current['variant']}",
        "",
        f"**Estimated savings vs prior offer:** {usd(fin_save, signed=True)} if financed "
        f"({current['term_months']} mo, {usd(current['down_payment'])} down) · "
        f"{usd(cash_save, signed=True)} if paid in cash.",
        f"Monthly payment {usd(prior['monthly_payment'])} → {usd(current['monthly_payment'])} "
        f"({usd(pay_delta, signed=True)}/mo).",
        "",
        "### What changed",
        *[f"- {c}" for c in changes],
        "",
        "### Prior vs. now",
        "| | Prior offer | Now |",
        "|---|---:|---:|",
        _row("Vehicle price (after Tesla discount)", usd(prior["vehicle_price"]), usd(current["vehicle_price"])),
        _row("Destination + order fee", usd(prior["destination_fee"] + prior["order_fee"]),
             usd(current["destination_fee"] + current["order_fee"])),
        _row("Cash incentives", usd(sum(i["amount"] for i in prior["cash_incentives"])),
             usd(sum(i["amount"] for i in current["cash_incentives"]))),
        _row("Perks (valued)", usd(sum(i["value"] for i in prior["perks"])),
             usd(sum(i["value"] for i in current["perks"]))),
        _row("**Cash price, pre-tax (after perks)**", usd(prior["metrics"]["cash_price"]),
             usd(current["metrics"]["cash_price"])),
        _row("APR / term", _apr_text(prior), _apr_text(current)),
        _row("Monthly payment", usd(prior["monthly_payment"]), usd(current["monthly_payment"])),
        _row("Total interest", usd(prior["total_interest"]), usd(current["total_interest"])),
        _row("**Financed total (after perks)**", usd(prior["metrics"]["financed_total"]),
             usd(current["metrics"]["financed_total"])),
        "",
    ]
    if apr_changed or current["apr"]["source"] != "fallback":
        lines += [
            "### Financing terms",
            f"- {current['apr']['label']} (source: {current['apr']['source']})",
            f"- Scenario: {usd(current['down_payment'])} down, {usd(current['amount_financed'])} financed "
            f"for {current['term_months']} months.",
        ]
        if current["apr"].get("evidence"):
            lines.append(f"- Tesla's wording: “{current['apr']['evidence']}”")
        lines.append("")
    lines += [
        "### Cheapest matching vehicle",
        f"- {v.get('year')} {v.get('trim')} — {v.get('paint')} / {v.get('wheels')} / {v.get('interior')}",
        f"- {v.get('location') or 'location not listed'} · {current['inventory_count']} matching in inventory"
        + (" · demo vehicle" if v.get("is_demo") else ""),
        f"- {v.get('url')}",
        "",
        "_Prices exclude taxes, registration, and state incentives. Savings compare against the offer at the "
        "last alert (or the last time the deal got worse)._",
    ]
    if site_url:
        lines.append(f"\nDashboard: {site_url}")
    return "\n".join(lines)


class GitHub:
    def __init__(self, repo: str, token: str):
        self.repo, self.token = repo, token

    @classmethod
    def from_env(cls) -> "GitHub | None":
        repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
        return cls(repo, token) if repo and token else None

    def _call(self, method: str, path: str, payload: dict | None = None):
        req = urllib.request.Request(
            f"https://api.github.com/repos/{self.repo}{path}",
            method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            return json.loads(body) if body else None

    def ensure_label(self, name: str, color: str, description: str) -> None:
        try:
            self._call("POST", "/labels", {"name": name, "color": color, "description": description})
        except urllib.error.HTTPError as exc:
            if exc.code != 422:  # 422 = already exists
                raise

    def create_issue(self, title: str, body: str, label: str) -> str:
        return self._call("POST", "/issues", {"title": title, "body": body, "labels": [label]})["html_url"]

    def open_issues(self, label: str) -> list[dict]:
        return self._call("GET", f"/issues?labels={label}&state=open&per_page=20") or []

    def comment(self, number: int, body: str) -> None:
        self._call("POST", f"/issues/{number}/comments", {"body": body})

    def close(self, number: int) -> None:
        self._call("PATCH", f"/issues/{number}", {"state": "closed", "state_reason": "completed"})

"""Best-effort promotional APR detection from Tesla's public design page.

Tesla advertises promotional financing in disclaimer copy such as
"1.99% APR available for Model Y Premium Rear-Wheel Drive and Premium All-Wheel
Drive for up to 72 months...". We look for that copy in the page (including the
JSON blobs the page embeds) and only accept a rate whose own clause names the
tracked trim. Anything ambiguous is dropped; offers.toml is the manual fallback.
"""

from __future__ import annotations

import html
import re
from typing import Callable

from ..fetch import fetch_text

DESIGN_URLS = {"my": "https://www.tesla.com/modely/design", "m3": "https://www.tesla.com/model3/design"}

APR_RE = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*%\s*APR", re.I)
# Sentence ends and JSON string delimiters both bound a clause.
BOUNDARY_RE = re.compile(r'[.!?]["\']?\s|["{}\[\]|]')
TERM_RE = re.compile(r"(\d{2})[\s-]*(?:month|mo\b)", re.I)
MODEL_NAMES = {"my": "model y", "m3": "model 3"}


def page_text(raw: str) -> str:
    """Flatten HTML + embedded JSON strings into searchable plain text."""
    text = raw
    # Decode JSON-style escapes (%, /, \") that hide copy inside script tags.
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
    text = text.replace('\\"', '"').replace("\\n", " ").replace("\\/", "/")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text)


def extract_aprs(text: str, tracker: dict) -> dict[str, dict]:
    """Return {variant: {"rate", "max_term_months", "evidence"}} for the tracked trim."""
    include = [t.lower() for t in tracker.get("trim_include", [])]
    model_name = MODEL_NAMES.get(tracker["model"], "")
    matches = list(APR_RE.finditer(text))
    bounds = [b.end() for b in BOUNDARY_RE.finditer(text)]
    found: dict[str, dict] = {}
    for i, m in enumerate(matches):
        # The clause is this rate's own sentence, cut short by any neighbouring rate.
        start = max([b for b in bounds if b <= m.start()] + [matches[i - 1].end() if i else 0])
        end = min([b for b in bounds if b > m.end()] + [len(text), m.end() + 400])
        if i + 1 < len(matches):
            end = min(end, matches[i + 1].start())
        clause = text[start:end]
        lower = clause.lower()
        if model_name and model_name not in lower:
            continue
        if include and not any(t in lower for t in include):
            continue
        variants = []
        if re.search(r"rear-wheel|\brwd\b", lower):
            variants.append("RWD")
        if re.search(r"all-wheel|\bawd\b", lower):
            variants.append("AWD")
        variants = variants or list(tracker.get("variants", ["RWD", "AWD"]))
        term = TERM_RE.search(clause)
        rate = float(m.group(1))
        for variant in variants:
            prior = found.get(variant)
            if prior is None or rate < prior["rate"]:
                found[variant] = {
                    "rate": rate,
                    "max_term_months": int(term.group(1)) if term else None,
                    "evidence": clause.strip()[:300],
                }
    return found


def collect(tracker: dict, fetch: Callable[[str], str] = fetch_text) -> dict[str, dict]:
    url = DESIGN_URLS.get(tracker["model"])
    if not url or tracker["condition"] != "new":
        return {}
    return extract_aprs(page_text(fetch(url)), tracker)


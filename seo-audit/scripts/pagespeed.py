#!/usr/bin/env python3
"""Google PageSpeed Insights wrapper.

Calls the public PSI v5 API for a URL and returns Core Web Vitals (lab +
field), Lighthouse category scores, and the top opportunities.

Auth: anonymous works but is rate-limited. Set PSI_API_KEY for higher quota.
Get a free key at https://developers.google.com/speed/docs/insights/v5/get-started

Usage:
  pagespeed.py <url> [--strategy mobile|desktop] [--out file.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def run(url: str, strategy: str = "mobile", api_key: str | None = None) -> dict:
    params = [
        ("url", url),
        ("strategy", strategy),
        ("category", "performance"),
        ("category", "seo"),
        ("category", "accessibility"),
        ("category", "best-practices"),
    ]
    if api_key:
        params.append(("key", api_key))

    full = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"User-Agent": "seo-audit/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        msg = ""
        try:
            msg = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        if exc.code == 429:
            sys.stderr.write(
                "PSI rate-limited (HTTP 429). Anonymous quota is small.\n"
                "Get a free API key and set PSI_API_KEY:\n"
                "  https://developers.google.com/speed/docs/insights/v5/get-started\n"
            )
        sys.stderr.write(f"PSI error {exc.code}: {msg}\n")
        sys.exit(1)

    lh = data.get("lighthouseResult") or {}
    audits = lh.get("audits") or {}
    cats = lh.get("categories") or {}
    loadex = data.get("loadingExperience") or {}

    def metric(key: str) -> dict:
        a = audits.get(key) or {}
        return {
            "value": a.get("numericValue"),
            "unit": a.get("numericUnit"),
            "displayValue": a.get("displayValue"),
            "score": a.get("score"),
        }

    opportunities = []
    for key, audit in audits.items():
        details = audit.get("details") or {}
        if details.get("type") != "opportunity":
            continue
        savings = (audit.get("numericValue") or 0)
        if savings <= 0:
            continue
        opportunities.append({
            "id": key,
            "title": audit.get("title"),
            "description": audit.get("description"),
            "estimated_savings_ms": savings,
            "displayValue": audit.get("displayValue"),
        })
    opportunities.sort(key=lambda o: o["estimated_savings_ms"], reverse=True)

    return {
        "url": url,
        "strategy": strategy,
        "scores": {
            "performance": (cats.get("performance") or {}).get("score"),
            "seo": (cats.get("seo") or {}).get("score"),
            "accessibility": (cats.get("accessibility") or {}).get("score"),
            "best_practices": (cats.get("best-practices") or {}).get("score"),
        },
        "lab": {
            "first_contentful_paint": metric("first-contentful-paint"),
            "largest_contentful_paint": metric("largest-contentful-paint"),
            "total_blocking_time": metric("total-blocking-time"),
            "cumulative_layout_shift": metric("cumulative-layout-shift"),
            "speed_index": metric("speed-index"),
            "interactive": metric("interactive"),
        },
        "field": {
            "metrics": loadex.get("metrics") or {},
            "overall_category": loadex.get("overall_category"),
        } if loadex else None,
        "top_opportunities": opportunities[:8],
    }


def main() -> None:
    p = argparse.ArgumentParser(description="PageSpeed Insights wrapper.")
    p.add_argument("url")
    p.add_argument("--strategy", default="mobile", choices=["mobile", "desktop"])
    p.add_argument("--out", "-o", default="-")
    args = p.parse_args()

    api_key = os.environ.get("PSI_API_KEY") or None
    out = run(args.url, args.strategy, api_key)

    text = json.dumps(out, indent=2, ensure_ascii=False)
    if args.out == "-":
        sys.stdout.write(text + "\n")
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        sys.stderr.write(f"Wrote {args.out}\n")


if __name__ == "__main__":
    main()

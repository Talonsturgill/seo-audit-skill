#!/usr/bin/env python3
"""Render a saved audit JSON into a client-ready PDF.

Usage:
  report_pdf.py --input <audit.json> --output <report.pdf>
  report_pdf.py --input <audit.json> --pagespeed <psi.json> --output <report.pdf>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

SEVERITY_HEX = {
    "high": "#d93025",
    "medium": "#f29900",
    "low": "#1a73e8",
    "info": "#5f6368",
}


def score_hex(score: int) -> str:
    if score >= 80:
        return "#188038"
    if score >= 60:
        return "#1a73e8"
    if score >= 40:
        return "#f29900"
    return "#d93025"


def percent(score):
    if score is None:
        return "—"
    return f"{round(score * 100)}"


def build(audit: dict, psi: dict | None, output_path: Path) -> None:
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=22,
                        spaceAfter=8, textColor=colors.HexColor("#202124"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=14,
                        spaceAfter=8, spaceBefore=14,
                        textColor=colors.HexColor("#202124"))
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10,
                          leading=14)
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=8,
                           leading=11, textColor=colors.HexColor("#5f6368"))

    target = audit.get("target") or "(unknown)"
    crawled_at = audit.get("crawled_at") or ""
    analysis = audit.get("analysis") or {}
    score = analysis.get("score") or 0
    stats = analysis.get("stats") or {}
    issues = analysis.get("issues") or []

    story: list = []
    story.append(Paragraph(f"SEO Audit — {target}", h1))
    story.append(Paragraph(f"Crawled: {crawled_at}", small))
    story.append(Spacer(1, 0.15 * inch))

    story.append(Paragraph(
        f"<b>Overall Score:</b> "
        f"<font color='{score_hex(score)}' size='16'><b>{score}/100</b></font>",
        body,
    ))

    story.append(Paragraph("Crawl Stats", h2))
    stats_rows = [
        ["Pages crawled", str(stats.get("pages_crawled", 0))],
        ["HTML pages parsed", str(stats.get("html_pages", 0))],
        ["Avg response time (ms)", str(stats.get("avg_response_time_ms", 0))],
        ["Avg word count", str(stats.get("avg_word_count", 0))],
        ["Total issues", str(analysis.get("issue_count", 0))],
    ]
    t = Table(stats_rows, colWidths=[2.5 * inch, 1.5 * inch])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dadce0")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f3f4")),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)

    if psi:
        story.append(Paragraph("PageSpeed Insights", h2))
        psi_scores = psi.get("scores") or {}
        psi_rows = [
            ["Strategy", psi.get("strategy", "mobile")],
            ["Performance", percent(psi_scores.get("performance"))],
            ["SEO", percent(psi_scores.get("seo"))],
            ["Accessibility", percent(psi_scores.get("accessibility"))],
            ["Best Practices", percent(psi_scores.get("best_practices"))],
        ]
        lab = psi.get("lab") or {}
        for key, label in [
            ("largest_contentful_paint", "LCP"),
            ("cumulative_layout_shift", "CLS"),
            ("total_blocking_time", "TBT"),
            ("first_contentful_paint", "FCP"),
        ]:
            m = lab.get(key) or {}
            psi_rows.append([label, m.get("displayValue") or "—"])

        pt = Table(psi_rows, colWidths=[2.5 * inch, 1.5 * inch])
        pt.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dadce0")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f3f4")),
            ("PADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(pt)

        opps = psi.get("top_opportunities") or []
        if opps:
            story.append(Spacer(1, 0.1 * inch))
            story.append(Paragraph("<b>Top performance opportunities</b>", body))
            for opp in opps[:5]:
                savings_s = round(opp.get("estimated_savings_ms", 0) / 1000, 1)
                story.append(Paragraph(
                    f"• <b>{opp.get('title')}</b> — save ~{savings_s}s",
                    body,
                ))

    story.append(Paragraph("Issues by Severity", h2))
    by_sev: dict[str, list] = {"high": [], "medium": [], "low": [], "info": []}
    for issue in issues:
        by_sev.setdefault(issue.get("severity", "info"), []).append(issue)

    if not issues:
        story.append(Paragraph("No issues detected. 🎉", body))

    for sev in ("high", "medium", "low", "info"):
        items = by_sev.get(sev) or []
        if not items:
            continue
        story.append(Paragraph(
            f"<font color='{SEVERITY_HEX[sev]}'><b>"
            f"{sev.upper()} ({len(items)})"
            f"</b></font>",
            body,
        ))
        for issue in items:
            story.append(Paragraph(
                f"• <b>{issue.get('type')}</b>: {issue.get('message')}",
                body,
            ))
        story.append(Spacer(1, 0.08 * inch))

    if issues:
        story.append(PageBreak())
        story.append(Paragraph("Issue Detail", h2))
        for issue in issues:
            sev = issue.get("severity", "info")
            story.append(Paragraph(
                f"<font color='{SEVERITY_HEX[sev]}'><b>"
                f"[{sev.upper()}] {issue.get('type')}"
                f"</b></font>",
                body,
            ))
            story.append(Paragraph(issue.get("message", ""), body))
            examples = issue.get("examples") or []
            if examples:
                for ex in examples[:5]:
                    if isinstance(ex, dict):
                        line = " — ".join(f"{k}: {v}" for k, v in ex.items())
                    else:
                        line = str(ex)
                    story.append(Paragraph(f"&nbsp;&nbsp;&nbsp;◦ {line}", small))
            story.append(Spacer(1, 0.08 * inch))

    doc = SimpleDocTemplate(
        str(output_path), pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        title=f"SEO Audit — {target}",
    )
    doc.build(story)


def main() -> None:
    p = argparse.ArgumentParser(description="Render audit JSON into PDF.")
    p.add_argument("--input", "-i", required=True, help="Audit JSON path")
    p.add_argument("--output", "-o", required=True, help="PDF output path")
    p.add_argument("--pagespeed", help="Optional PSI JSON path")
    args = p.parse_args()

    audit = json.loads(Path(args.input).read_text(encoding="utf-8"))
    psi = None
    if args.pagespeed:
        psi = json.loads(Path(args.pagespeed).read_text(encoding="utf-8"))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    build(audit, psi, output)
    sys.stderr.write(f"Wrote {output}\n")


if __name__ == "__main__":
    main()

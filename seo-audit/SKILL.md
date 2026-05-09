---
name: seo-audit
description: >
  Free, self-hosted technical SEO audit. Crawls a domain (no third-party API),
  extracts on-page SEO signals across all crawled pages, optionally pulls Core
  Web Vitals from Google PageSpeed Insights, and renders a client-ready PDF.
  Use when the user says "seo audit", "site audit", "technical seo", "crawl my
  site", "audit my site", or passes a domain / URL for SEO analysis.
allowed-tools: Bash, Read, Write
---

# seo-audit — Free Technical SEO Audit

A self-contained crawler + analyzer + PDF reporter. No paid APIs. Runs
against any public domain.

## Components

| Script | What it does |
|--------|--------------|
| `scripts/audit.py crawl <domain>` | Async same-host crawl, extracts on-page signals, surfaces site-wide issues, scores 0-100 |
| `scripts/audit.py page <url>` | Single-page audit (no crawl) |
| `scripts/pagespeed.py <url>` | Google PageSpeed Insights — Core Web Vitals + perf score |
| `scripts/report_pdf.py` | Renders audit JSON into a client-ready PDF |

## Phase 0: Ensure Dependencies (run once per session)

Python deps must be available before the scripts work. On first run within
a session, run:

```bash
pip install -q -r requirements.txt
```

If `requirements.txt` isn't reachable from the cwd, install directly:

```bash
pip install -q "httpx[http2]>=0.27" "selectolax>=0.3.21" "reportlab>=4.0"
```

If `selectolax` fails to install (rare — needs a Linux/macOS wheel), fall
back to `beautifulsoup4` and tell the user the parser swap may slow the
crawl ~2x.

## Standard Workflow — `/seo-audit <domain>`

### Step 1 — Run the crawl

```bash
mkdir -p output
python3 scripts/audit.py crawl <domain> \
    --max-pages 50 --max-depth 3 \
    --out output/<domain>-audit.json
```

Crawl takes 30–90s depending on the site. Default 50 pages, depth 3, 10
concurrent fetches. Increase `--max-pages` for larger audits (100, 250).
The script prints progress to stderr and writes JSON to `--out`.

### Step 2 — (Optional) PageSpeed Insights for the homepage

```bash
python3 scripts/pagespeed.py "https://<domain>/" \
    --strategy mobile \
    --out output/<domain>-psi.json
```

Run in **parallel** with the crawl when possible (separate Bash call). Uses
the public PSI API anonymously; if `PSI_API_KEY` is set, uses it for higher
quotas. ~30s per call. Skip if rate-limited (HTTP 429) or if the user
just wants a fast crawl-only report.

### Step 3 — Read both JSONs and write the report

Read the audit JSON (and PSI JSON if you ran it). Write a tight markdown
summary with this structure:

```
## SEO Audit — <domain>
**Score: <N>/100**  •  <pages_crawled> pages crawled  •  <total_issues> issues

### Top 3 actions this week
1. <highest-impact issue with concrete fix>
2. <second>
3. <third>

### Issues
🔴 HIGH (<count>)
- <type>: <message>

🟡 MEDIUM (<count>)
- <type>: <message>

🔵 LOW (<count>)
- <type>: <message>

### Stats
- Avg TTFB: <ms>ms
- Avg word count: <n>
- Sitemap: <found / missing>
- robots.txt: <found / missing>

### PageSpeed (if available)
- Performance: <score>/100
- LCP: <value>  •  CLS: <value>  •  TBT: <value>
- Top opportunity: <title> (~<savings>s)
```

Use these severity badges: 🔴 high, 🟡 medium, 🔵 low, ⚪ info. Score colors:
🟢 80+, 🔵 60-79, 🟡 40-59, 🔴 <40.

### Step 4 — Offer the PDF

After printing the summary, ask:

> Want a PDF deliverable? I'll render it from the audit JSON.

If yes:

```bash
python3 scripts/report_pdf.py \
    --input output/<domain>-audit.json \
    --pagespeed output/<domain>-psi.json \
    --output output/<domain>-report.pdf
```

(Drop `--pagespeed` if you didn't run PSI.) When done, surface the PDF as
a downloadable file to the user.

## Single-page mode

If the user passes a specific URL instead of a domain (e.g. they want to
audit one landing page deeply):

```bash
python3 scripts/audit.py page "<url>"
python3 scripts/pagespeed.py "<url>" --strategy mobile
```

Run both in parallel, then synthesize.

## Output Conventions

- Save audit JSON to `output/<domain>-audit.json`
- Always lead with the executive summary (≤10 lines), then detail
- Always end with "Top 3 actions this week" — concrete, specific fixes
- Don't restate the raw JSON unless the user asks

## What This Skill Covers

- Crawl + on-page signals (title, meta, h1, canonical, robots, OG, schema)
- Site-wide issues (broken links, redirects, dupes, thin content, alt text)
- robots.txt + sitemap.xml validation
- Response time / TTFB sampling
- Core Web Vitals (PSI)
- PDF deliverable

## What This Skill Does NOT Cover

- Keyword research / search volume — needs Google Ads API or paid data
- Backlink profile — needs a web-scale link index (Ahrefs/DataForSEO)
- Competitor SERP overlap — same data dependency
- Live Google rank checking — needs SERP API or scraping

If the user asks for any of those, say it's out of scope for this skill and
explain the data dependency. Don't try to fake it.

## Tunables

- `--max-pages`: 50 default, 250 for medium sites, 1000 for large (slow)
- `--max-depth`: 3 default. Use 5+ for deep info architectures, 2 for
  shallow brochure sites
- `--concurrency`: 10 default. Lower (3-5) for fragile/small servers,
  higher (20-30) for big sites with good infra
- PSI strategy: `mobile` is the default Google uses for ranking; run
  `desktop` separately if user asks
- `PSI_API_KEY` env var: optional, raises PSI quota past the anonymous limit

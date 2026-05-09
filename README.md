# seo-audit

> A free, self-hosted technical SEO audit. Crawls a domain, extracts on-page
> SEO signals, optionally pulls Core Web Vitals from Google PageSpeed Insights,
> and renders a client-ready PDF — all without paid third-party APIs.

A single command — `/seo-audit yourdomain.com` — runs the full audit,
summarizes findings, and produces a deliverable.

## What it does

- **Async same-host crawl** — up to N pages with configurable depth and concurrency
- **Per-page signal extraction**: title, meta description, h1/h2, canonical,
  robots, viewport, html lang, Open Graph, Twitter Cards, JSON-LD schema types,
  hreflang, image alt coverage, internal/external link counts, word count,
  response time, redirect chain
- **Site-wide issue detection** with severity scoring (0-100):
  - Broken pages (4xx/5xx)
  - Redirect chains > 1 hop
  - Missing / duplicate / over-long titles
  - Missing / duplicate / over-long meta descriptions
  - Missing or multiple `<h1>`
  - Missing canonical / viewport
  - `noindex` flags (meta or X-Robots-Tag)
  - Thin content (< 200 words)
  - Images missing alt text
  - Slow responses (> 2000 ms TTFB)
  - Missing robots.txt / sitemap.xml
- **robots.txt + sitemap.xml** discovery and validation (handles sitemap indexes)
- **Google PageSpeed Insights** integration — Core Web Vitals (LCP, CLS, TBT,
  FCP), Lighthouse category scores, top performance opportunities
- **Client-ready PDF report** rendered with reportlab — score badge, stats,
  issues by severity, optional PSI section

## What it does NOT do

These require a web-scale crawl index or paid SEO data and are intentionally
out of scope:

- Keyword volume / CPC / difficulty (use Google Ads Keyword Planner API or paid SEO data)
- Backlink profile (needs an Ahrefs / DataForSEO-class link index)
- Competitor SERP overlap, content gap (same data dependency)
- Live Google rank tracking (needs a SERP API or scraping)

## Installation

### Claude.ai (web)

1. Download the `seo-audit/` folder from this repo (clone or download zip).
2. Zip just that folder: `zip -r seo-audit.zip seo-audit/`
3. In Claude.ai → **Settings → Capabilities → Skills → Upload skill**
4. Drop the zip.
5. Invoke from any chat: `/seo-audit example.com`

> **Note on web sandbox:** the skill makes outbound HTTP calls (to crawl the
> target site and to call PageSpeed Insights). Make sure your code-execution
> environment allows outbound network requests.

### Claude Code (local CLI)

```bash
git clone https://github.com/Talonsturgill/seo-audit-skill.git
cd seo-audit-skill

# Drop the skill into Claude Code's skills directory
mkdir -p ~/.claude/skills
cp -r seo-audit ~/.claude/skills/

# Set up isolated Python env for the skill
cd ~/.claude/skills/seo-audit
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# (Optional) Pin the shebangs to the venv so /seo-audit can call scripts directly
sed -i "1s|.*|#!$HOME/.claude/skills/seo-audit/.venv/bin/python3|" scripts/*.py
chmod +x scripts/*.py
```

Then in Claude Code: `/seo-audit example.com`

## Usage

```
/seo-audit example.com
```

The skill will:

1. Run the crawler (default: 50 pages, depth 3)
2. (Optional) Pull PageSpeed Insights on the homepage in parallel
3. Print an executive summary with score, top issues, and three concrete
   actions for the week
4. Offer to render a PDF deliverable

You can also run any of the scripts manually:

```bash
# Full crawl
python3 scripts/audit.py crawl example.com --max-pages 100 --out output/audit.json

# Single page
python3 scripts/audit.py page https://example.com/landing-page

# PageSpeed Insights
python3 scripts/pagespeed.py "https://example.com/" --strategy mobile

# PDF from saved JSON
python3 scripts/report_pdf.py --input output/audit.json --output output/report.pdf
```

## Using a PageSpeed Insights API key (optional)

The PSI step is optional — without a key, the script still works but Google
rate-limits anonymous calls (HTTP 429) after a few requests. A free key
removes that limit.

**Get a key (free, 30 seconds, no billing):**
https://developers.google.com/speed/docs/insights/v5/get-started

The script reads it from the `PSI_API_KEY` environment variable. There's no
config file or auth flow — it's just an env var.

### On Claude.ai (web)

There's no per-skill secrets UI, so you have a few options:

1. **Skip it.** Run audits without a key. If PSI rate-limits, the skill
   surfaces a crawl-only report. Fine for occasional use.
2. **Paste the key per session.** When the skill hits a 429, it'll ask
   for a key. Reply with something like `PSI_API_KEY=AIzaSyXxxx...` and it
   re-runs PSI with that key. Lost when the chat ends.
3. **Stash it in your profile for persistence.** Add a line to your
   Claude.ai personal preferences (Settings → Profile) or a Project's
   custom instructions:
   > *"My PSI API key is `AIzaSyXxxx...`. Use it whenever the seo-audit
   > skill needs PSI."*
   
   Claude reads that context every chat — no re-pasting.

The PSI key is low-risk (it can only consume your free quota — it can't
spend money), but treat it as a secret regardless.

### On Claude Code (local CLI)

Just export it in your shell:

```bash
export PSI_API_KEY=AIzaSyXxxx...
```

Add it to `~/.bashrc` / `~/.zshrc` to persist.

## Configuration

| Flag / Env | Default | What it does |
|------------|---------|--------------|
| `--max-pages` | 50 | Cap on pages crawled |
| `--max-depth` | 3 | Max link depth from the homepage |
| `--concurrency` | 10 | Parallel HTTP fetches |
| `--strategy` (PSI) | `mobile` | `mobile` or `desktop` |
| `PSI_API_KEY` | unset | Free Google API key for higher PSI quotas — [get one here](https://developers.google.com/speed/docs/insights/v5/get-started) |

## Scoring rubric

The site score starts at 100 and subtracts:

- 12 points per **high** severity issue (broken pages, missing/duplicate titles)
- 6 points per **medium** issue (missing meta description, missing h1, slow responses, thin content, redirect chains, missing sitemap)
- 2 points per **low** issue (title length, missing canonical, multiple h1, alt text minor)
- 0 points for **info** items (e.g. intentional `noindex`)

Floored at 0.

## Output structure

```
output/
├── <domain>-audit.json      # full crawl + analysis
├── <domain>-psi.json        # PageSpeed Insights (if run)
└── <domain>-report.pdf      # final deliverable
```

## Dependencies

- Python 3.10+
- `httpx[http2]` — async HTTP
- `selectolax` — fast HTML parsing (lexbor backend)
- `reportlab` — PDF rendering

## License

[MIT](LICENSE)

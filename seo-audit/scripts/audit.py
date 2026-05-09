#!/usr/bin/env python3
"""SEO technical audit — async crawler + on-page analyzer.

Crawls a domain (same-host only) up to a page limit, extracts on-page SEO
signals, and surfaces site-wide issues. No external API needed.

Usage:
  audit.py crawl <domain>  [--max-pages 50] [--max-depth 3] [--out file.json]
  audit.py page  <url>     [--out file.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from xml.etree import ElementTree as ET

import httpx
from selectolax.parser import HTMLParser

USER_AGENT = "Mozilla/5.0 (compatible; SeoAuditBot/1.0)"
DEFAULT_TIMEOUT = 20
DEFAULT_CONCURRENCY = 10
DEFAULT_MAX_PAGES = 50
DEFAULT_MAX_DEPTH = 3


def normalize_domain(value: str) -> str:
    value = value.strip()
    for prefix in ("https://", "http://"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
    value = value.split("/", 1)[0].lower().rstrip(".")
    if value.startswith("www."):
        value = value[4:]
    return value


def normalize_url(url: str) -> str:
    p = urlparse(url)
    path = p.path or "/"
    return urlunparse((p.scheme, p.netloc.lower(), path, p.params, p.query, ""))


def host_of(url: str) -> str:
    h = urlparse(url).netloc.lower()
    if h.startswith("www."):
        h = h[4:]
    return h


def extract_signals(html: str, url: str) -> dict:
    if not html:
        return {}
    tree = HTMLParser(html)

    def text_of(node):
        return node.text(strip=True) if node else None

    def attr_of(selector, attr):
        node = tree.css_first(selector)
        return node.attributes.get(attr) if node else None

    title = text_of(tree.css_first("title"))
    meta_desc = attr_of('meta[name="description"]', "content")
    canonical = attr_of('link[rel="canonical"]', "href")
    robots = attr_of('meta[name="robots"]', "content")
    viewport = attr_of('meta[name="viewport"]', "content")
    lang = attr_of("html", "lang")

    h1s = [text_of(n) for n in tree.css("h1") if text_of(n)]
    h2s = [text_of(n) for n in tree.css("h2") if text_of(n)]

    og = {}
    for n in tree.css('meta[property^="og:"]'):
        key = n.attributes.get("property", "")
        og[key] = n.attributes.get("content")

    twitter = {}
    for n in tree.css('meta[name^="twitter:"]'):
        key = n.attributes.get("name", "")
        twitter[key] = n.attributes.get("content")

    schemas = []
    for n in tree.css('script[type="application/ld+json"]'):
        raw = (n.text() or "").strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            schemas.append("<invalid-json>")
            continue
        items = obj if isinstance(obj, list) else [obj]
        for item in items:
            if isinstance(item, dict):
                t = item.get("@type")
                if isinstance(t, list):
                    schemas.extend(str(x) for x in t)
                elif t:
                    schemas.append(str(t))

    hreflang = []
    for n in tree.css('link[rel="alternate"][hreflang]'):
        hreflang.append({
            "lang": n.attributes.get("hreflang"),
            "href": n.attributes.get("href"),
        })

    images = tree.css("img")
    img_total = len(images)
    img_missing_alt = sum(
        1 for n in images if not (n.attributes.get("alt") or "").strip()
    )

    page_host = host_of(url)
    internal, external = set(), 0
    for n in tree.css("a[href]"):
        href = (n.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            continue
        absolute = urljoin(url, href)
        scheme = urlparse(absolute).scheme
        if scheme not in ("http", "https"):
            continue
        if host_of(absolute) == page_host:
            internal.add(normalize_url(absolute))
        else:
            external += 1

    body = tree.body
    visible = body.text(separator=" ", strip=True) if body else ""
    word_count = len(re.findall(r"\b\w+\b", visible))

    return {
        "title": title,
        "title_length": len(title) if title else 0,
        "meta_description": meta_desc,
        "meta_description_length": len(meta_desc) if meta_desc else 0,
        "h1": h1s,
        "h1_count": len(h1s),
        "h2_count": len(h2s),
        "canonical": canonical,
        "robots": robots,
        "viewport": viewport,
        "html_lang": lang,
        "open_graph": og,
        "twitter": twitter,
        "schema_types": schemas,
        "hreflang": hreflang,
        "image_count": img_total,
        "images_missing_alt": img_missing_alt,
        "internal_links": sorted(internal),
        "internal_link_count": len(internal),
        "external_link_count": external,
        "word_count": word_count,
    }


async def fetch(client: httpx.AsyncClient, url: str) -> dict:
    start = time.monotonic()
    try:
        resp = await client.get(url, follow_redirects=True)
    except httpx.RequestError as e:
        return {
            "url": url,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "status_code": None,
            "response_time_ms": round((time.monotonic() - start) * 1000),
        }
    elapsed_ms = round((time.monotonic() - start) * 1000)

    chain = [str(h.url) for h in resp.history] + [str(resp.url)]
    content_type = resp.headers.get("content-type", "")
    is_html = "html" in content_type.lower()

    return {
        "url": url,
        "ok": True,
        "status_code": resp.status_code,
        "final_url": str(resp.url),
        "redirect_chain": chain if len(chain) > 1 else [],
        "response_time_ms": elapsed_ms,
        "content_type": content_type,
        "content_length": len(resp.content),
        "html": resp.text if is_html and resp.status_code < 400 else None,
        "x_robots_tag": resp.headers.get("x-robots-tag"),
    }


async def crawl_site(start_url: str, max_pages: int, max_depth: int,
                    concurrency: int) -> list[dict]:
    host = host_of(start_url)
    visited: set[str] = set()
    queued: set[str] = {normalize_url(start_url)}
    queue: list[tuple[str, int]] = [(start_url, 0)]
    pages: list[dict] = []

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.5",
        "Accept-Language": "en-US,en;q=0.9",
    }
    timeout = httpx.Timeout(DEFAULT_TIMEOUT)
    limits = httpx.Limits(max_connections=concurrency * 2,
                          max_keepalive_connections=concurrency)

    async with httpx.AsyncClient(headers=headers, timeout=timeout,
                                  limits=limits, http2=True) as client:
        sem = asyncio.Semaphore(concurrency)

        async def task(url: str, depth: int):
            async with sem:
                return depth, await fetch(client, url)

        while queue and len(visited) < max_pages:
            batch = []
            while queue and len(batch) < concurrency and len(visited) < max_pages:
                url, depth = queue.pop(0)
                norm = normalize_url(url)
                if norm in visited:
                    continue
                visited.add(norm)
                batch.append((url, depth))
            if not batch:
                break

            results = await asyncio.gather(*[task(u, d) for u, d in batch])

            for depth, result in results:
                page = {
                    "url": result["url"],
                    "depth": depth,
                    "fetch": {k: v for k, v in result.items() if k != "html"},
                    "signals": {},
                }
                if result.get("ok") and result.get("html"):
                    page["signals"] = extract_signals(result["html"], result["url"])
                    if depth < max_depth:
                        for link in page["signals"].get("internal_links", []):
                            norm = normalize_url(link)
                            if norm in visited or norm in queued:
                                continue
                            if host_of(link) != host:
                                continue
                            queue.append((link, depth + 1))
                            queued.add(norm)
                pages.append(page)

    return pages


def fetch_robots_and_sitemap(domain: str) -> dict:
    base = f"https://{domain}"
    out: dict = {"robots_txt": None, "sitemaps": []}
    headers = {"User-Agent": USER_AGENT}

    with httpx.Client(headers=headers, timeout=15, follow_redirects=True) as client:
        try:
            r = client.get(f"{base}/robots.txt")
            if r.status_code == 200 and "html" not in r.headers.get("content-type", "").lower():
                out["robots_txt"] = {
                    "status": r.status_code,
                    "size": len(r.text),
                    "content": r.text[:8000],
                    "sitemap_urls": re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text),
                }
            else:
                out["robots_txt"] = {"status": r.status_code, "missing": True}
        except Exception as e:
            out["robots_txt"] = {"error": str(e)}

        candidates = [f"{base}/sitemap.xml"]
        if isinstance(out["robots_txt"], dict):
            for u in out["robots_txt"].get("sitemap_urls") or []:
                if u not in candidates:
                    candidates.append(u)

        for sm_url in candidates[:5]:
            try:
                r = client.get(sm_url)
                if r.status_code != 200:
                    out["sitemaps"].append({
                        "url": sm_url, "status": r.status_code, "missing": True,
                    })
                    continue
                try:
                    root = ET.fromstring(r.content)
                except ET.ParseError as e:
                    out["sitemaps"].append({"url": sm_url, "error": f"parse: {e}"})
                    continue
                tag = root.tag.split("}")[-1]
                if tag == "sitemapindex":
                    children = [el.text for el in root.findall("{*}sitemap/{*}loc") if el.text]
                    out["sitemaps"].append({
                        "url": sm_url, "type": "index",
                        "child_count": len(children),
                        "children": children[:50],
                    })
                elif tag == "urlset":
                    urls = [el.text for el in root.findall("{*}url/{*}loc") if el.text]
                    out["sitemaps"].append({
                        "url": sm_url, "type": "urlset",
                        "url_count": len(urls),
                        "sample": urls[:20],
                    })
                else:
                    out["sitemaps"].append({"url": sm_url, "error": f"unknown root: {tag}"})
            except Exception as e:
                out["sitemaps"].append({"url": sm_url, "error": str(e)})

    return out


def analyze(pages: list[dict], robots_data: dict) -> dict:
    issues: list[dict] = []
    crawled = [p for p in pages if p["fetch"].get("ok")]
    html_pages = [p for p in crawled if p.get("signals")]

    status_dist = Counter(
        p["fetch"]["status_code"] for p in crawled if p["fetch"].get("status_code")
    )

    broken = [p for p in crawled if (p["fetch"].get("status_code") or 0) >= 400]
    if broken:
        issues.append({
            "severity": "high", "type": "broken_pages",
            "count": len(broken),
            "message": f"{len(broken)} pages returned 4xx/5xx",
            "examples": [
                {"url": p["url"], "status": p["fetch"]["status_code"]}
                for p in broken[:10]
            ],
        })

    redirected = [p for p in crawled if len(p["fetch"].get("redirect_chain") or []) > 2]
    if redirected:
        issues.append({
            "severity": "medium", "type": "redirect_chains",
            "count": len(redirected),
            "message": f"{len(redirected)} pages have multi-hop redirect chains",
            "examples": [
                {"url": p["url"], "chain": p["fetch"]["redirect_chain"]}
                for p in redirected[:5]
            ],
        })

    missing_title = [p for p in html_pages if not p["signals"].get("title")]
    if missing_title:
        issues.append({
            "severity": "high", "type": "missing_title",
            "count": len(missing_title),
            "message": f"{len(missing_title)} pages missing <title>",
            "examples": [p["url"] for p in missing_title[:10]],
        })

    short_title = [p for p in html_pages
                   if p["signals"].get("title") and p["signals"]["title_length"] < 30]
    long_title = [p for p in html_pages if p["signals"].get("title_length", 0) > 60]
    if short_title:
        issues.append({
            "severity": "low", "type": "title_too_short",
            "count": len(short_title),
            "message": f"{len(short_title)} titles under 30 chars",
            "examples": [{"url": p["url"], "title": p["signals"]["title"]}
                         for p in short_title[:5]],
        })
    if long_title:
        issues.append({
            "severity": "low", "type": "title_too_long",
            "count": len(long_title),
            "message": f"{len(long_title)} titles over 60 chars (will truncate in SERPs)",
            "examples": [{"url": p["url"], "title": p["signals"]["title"]}
                         for p in long_title[:5]],
        })

    title_counter = Counter(
        p["signals"]["title"] for p in html_pages if p["signals"].get("title")
    )
    dup_titles = {t: c for t, c in title_counter.items() if c > 1}
    if dup_titles:
        issues.append({
            "severity": "high", "type": "duplicate_titles",
            "count": sum(dup_titles.values()),
            "unique_count": len(dup_titles),
            "message": f"{len(dup_titles)} titles duplicated across {sum(dup_titles.values())} pages",
            "examples": [{"title": t, "count": c}
                         for t, c in list(dup_titles.items())[:5]],
        })

    missing_desc = [p for p in html_pages if not p["signals"].get("meta_description")]
    if missing_desc:
        issues.append({
            "severity": "medium", "type": "missing_meta_description",
            "count": len(missing_desc),
            "message": f"{len(missing_desc)} pages missing meta description",
            "examples": [p["url"] for p in missing_desc[:10]],
        })

    long_desc = [p for p in html_pages
                 if p["signals"].get("meta_description_length", 0) > 160]
    if long_desc:
        issues.append({
            "severity": "low", "type": "meta_description_too_long",
            "count": len(long_desc),
            "message": f"{len(long_desc)} meta descriptions over 160 chars",
            "examples": [p["url"] for p in long_desc[:5]],
        })

    desc_counter = Counter(
        p["signals"]["meta_description"]
        for p in html_pages if p["signals"].get("meta_description")
    )
    dup_descs = {d: c for d, c in desc_counter.items() if c > 1}
    if dup_descs:
        issues.append({
            "severity": "medium", "type": "duplicate_meta_descriptions",
            "count": sum(dup_descs.values()),
            "unique_count": len(dup_descs),
            "message": (
                f"{len(dup_descs)} meta descriptions duplicated across "
                f"{sum(dup_descs.values())} pages"
            ),
        })

    no_h1 = [p for p in html_pages if p["signals"].get("h1_count", 0) == 0]
    multi_h1 = [p for p in html_pages if p["signals"].get("h1_count", 0) > 1]
    if no_h1:
        issues.append({
            "severity": "medium", "type": "missing_h1",
            "count": len(no_h1),
            "message": f"{len(no_h1)} pages missing <h1>",
            "examples": [p["url"] for p in no_h1[:5]],
        })
    if multi_h1:
        issues.append({
            "severity": "low", "type": "multiple_h1",
            "count": len(multi_h1),
            "message": f"{len(multi_h1)} pages have multiple <h1> tags",
            "examples": [p["url"] for p in multi_h1[:5]],
        })

    missing_canonical = [p for p in html_pages if not p["signals"].get("canonical")]
    if missing_canonical:
        issues.append({
            "severity": "low", "type": "missing_canonical",
            "count": len(missing_canonical),
            "message": f"{len(missing_canonical)} pages missing canonical link",
        })

    missing_viewport = [p for p in html_pages if not p["signals"].get("viewport")]
    if missing_viewport:
        issues.append({
            "severity": "medium", "type": "missing_viewport",
            "count": len(missing_viewport),
            "message": f"{len(missing_viewport)} pages missing viewport meta (mobile-unfriendly)",
            "examples": [p["url"] for p in missing_viewport[:5]],
        })

    noindex = [p for p in html_pages
               if (p["signals"].get("robots") or "").lower().find("noindex") >= 0
               or (p["fetch"].get("x_robots_tag") or "").lower().find("noindex") >= 0]
    if noindex:
        issues.append({
            "severity": "info", "type": "noindex",
            "count": len(noindex),
            "message": f"{len(noindex)} pages set to noindex",
            "examples": [p["url"] for p in noindex[:10]],
        })

    thin = [p for p in html_pages
            if 0 < p["signals"].get("word_count", 0) < 200]
    if thin:
        issues.append({
            "severity": "medium", "type": "thin_content",
            "count": len(thin),
            "message": f"{len(thin)} pages have under 200 words",
            "examples": [{"url": p["url"], "words": p["signals"]["word_count"]}
                         for p in thin[:5]],
        })

    total_images = sum(p["signals"].get("image_count", 0) for p in html_pages)
    missing_alts = sum(p["signals"].get("images_missing_alt", 0) for p in html_pages)
    if missing_alts:
        pct = (missing_alts / total_images * 100) if total_images else 0
        issues.append({
            "severity": "medium" if pct > 20 else "low",
            "type": "images_missing_alt",
            "count": missing_alts, "total": total_images,
            "message": f"{missing_alts}/{total_images} images missing alt text ({pct:.0f}%)",
        })

    slow = [p for p in crawled if (p["fetch"].get("response_time_ms") or 0) > 2000]
    if slow:
        issues.append({
            "severity": "medium", "type": "slow_response",
            "count": len(slow),
            "message": f"{len(slow)} pages took >2000ms TTFB",
            "examples": [{"url": p["url"], "ms": p["fetch"]["response_time_ms"]}
                         for p in slow[:5]],
        })

    rb = robots_data.get("robots_txt") or {}
    if rb.get("missing"):
        issues.append({
            "severity": "low", "type": "missing_robots_txt",
            "message": "/robots.txt returns non-200 — consider adding one",
        })

    sitemaps = robots_data.get("sitemaps") or []
    has_sitemap = any(s.get("type") in ("index", "urlset") for s in sitemaps)
    if not has_sitemap:
        issues.append({
            "severity": "medium", "type": "missing_sitemap",
            "message": "No valid sitemap.xml found at /sitemap.xml or referenced in robots.txt",
        })

    score = 100
    weights = {"high": 12, "medium": 6, "low": 2, "info": 0}
    for issue in issues:
        score -= weights.get(issue["severity"], 0)
    score = max(0, score)

    return {
        "score": score,
        "issue_count": len(issues),
        "issues": issues,
        "stats": {
            "pages_crawled": len(crawled),
            "html_pages": len(html_pages),
            "status_distribution": dict(status_dist),
            "avg_response_time_ms": (
                round(sum(p["fetch"].get("response_time_ms", 0) for p in crawled) / len(crawled))
                if crawled else 0
            ),
            "avg_word_count": (
                round(sum(p["signals"].get("word_count", 0) for p in html_pages) / len(html_pages))
                if html_pages else 0
            ),
        },
    }


def write_output(payload: dict, out_path: str) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if out_path == "-":
        sys.stdout.write(text + "\n")
    else:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(text, encoding="utf-8")
        sys.stderr.write(f"Wrote {out_path}\n")


def cmd_crawl(args: argparse.Namespace) -> None:
    domain = normalize_domain(args.target)
    start_url = f"https://{domain}/"

    sys.stderr.write(
        f"Crawling {start_url} (max {args.max_pages} pages, depth {args.max_depth}, "
        f"concurrency {args.concurrency})...\n"
    )

    robots_data = fetch_robots_and_sitemap(domain)
    pages = asyncio.run(crawl_site(
        start_url, args.max_pages, args.max_depth, args.concurrency
    ))
    analysis = analyze(pages, robots_data)

    payload = {
        "target": domain,
        "crawled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "max_pages": args.max_pages,
            "max_depth": args.max_depth,
            "concurrency": args.concurrency,
        },
        "robots": robots_data,
        "analysis": analysis,
        "pages": pages,
    }
    write_output(payload, args.out)


def cmd_page(args: argparse.Namespace) -> None:
    async def run():
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.5",
        }
        async with httpx.AsyncClient(headers=headers, timeout=DEFAULT_TIMEOUT,
                                      http2=True) as client:
            return await fetch(client, args.url)

    result = asyncio.run(run())
    payload = {
        "url": args.url,
        "fetch": {k: v for k, v in result.items() if k != "html"},
        "signals": extract_signals(result.get("html") or "", args.url) if result.get("html") else {},
    }
    write_output(payload, args.out)


def main() -> None:
    parser = argparse.ArgumentParser(description="SEO technical audit — crawler + analyzer.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("crawl", help="Crawl a site and produce a full audit JSON.")
    pc.add_argument("target", help="Domain (example.com) or URL")
    pc.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    pc.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    pc.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    pc.add_argument("--out", "-o", default="-", help="Output path or '-' for stdout")
    pc.set_defaults(func=cmd_crawl)

    pp = sub.add_parser("page", help="Audit a single URL (no crawl).")
    pp.add_argument("url")
    pp.add_argument("--out", "-o", default="-", help="Output path or '-' for stdout")
    pp.set_defaults(func=cmd_page)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

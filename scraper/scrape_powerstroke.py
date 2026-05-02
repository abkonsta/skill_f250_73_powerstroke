#!/usr/bin/env python3
"""
PowerStroke.org Scraper
=======================
Scrapes all 5 target subforums from powerstroke.org:

  1. 99-03 7.3L General Discussion  (/forums/99-03-7-3l-general-discussion.12/)
  2. 99-03 7.3L Powerstroke Problems (/forums/99-03-7-3l-powerstroke-problems.21/)
  3. 99-03 7.3L Interior Discussion  (/forums/99-03-7-3l-powerstroke-interior-discussion.24/)
  4. 99-03 7.3L Exterior Discussion  (/forums/99-03-7-3l-exterior-discussion.95/)
  5. 99-03 7.3L Tech Files           (/forums/99-03-7-3-tech-files.138/)

Engine: XenForo

Setup:
    pip install requests beautifulsoup4 lxml

Usage:
    python3 scrape_powerstroke.py                        # scrape all subforums
    python3 scrape_powerstroke.py --forums general problems  # specific subforums
    python3 scrape_powerstroke.py --max-pages 10 --max-threads 100  # limited test run
    python3 scrape_powerstroke.py --no-post-content      # index only (fast)

Login (if required):
    export COOKIE_POWERSTROKE="xf_session=abc123; xf_user=xyz456"
    python3 scrape_powerstroke.py
"""

import os
import re
import time
import json
import random
import logging
import argparse
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────
# CONFIGURATION — edit these or pass as CLI args
# ─────────────────────────────────────────────────────────────

DOMAIN      = "powerstroke.org"
OUTPUT_DIR  = Path("./output_powerstroke")
DELAY       = 0.75      # seconds between requests
MAX_PAGES   = None      # None = all index pages
MAX_THREADS = None      # None = all threads

# Paste your session cookie here, or set env var COOKIE_POWERSTROKE
COOKIE = os.environ.get("COOKIE_POWERSTROKE", "")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.powerstroke.org/",
}

# ─────────────────────────────────────────────────────────────
# SUBFORUM DEFINITIONS
# ─────────────────────────────────────────────────────────────

SUBFORUMS = {
    "general": {
        "name":  "general_discussion",
        "label": "99-03 7.3L General Discussion",
        "url":   "https://www.powerstroke.org/forums/99-03-7-3l-general-discussion.12/",
    },
    "problems": {
        "name":  "powerstroke_problems",
        "label": "99-03 7.3L Powerstroke Problems",
        "url":   "https://www.powerstroke.org/forums/99-03-7-3l-powerstroke-problems.21/",
    },
    "interior": {
        "name":  "interior_discussion",
        "label": "99-03 7.3L Interior Discussion",
        "url":   "https://www.powerstroke.org/forums/99-03-7-3l-powerstroke-interior-discussion.24/",
    },
    "exterior": {
        "name":  "exterior_discussion",
        "label": "99-03 7.3L Exterior Discussion",
        "url":   "https://www.powerstroke.org/forums/99-03-7-3l-exterior-discussion.95/",
    },
    "tech": {
        "name":  "tech_files",
        "label": "99-03 7.3L Tech Files",
        "url":   "https://www.powerstroke.org/forums/99-03-7-3-tech-files.138/",
    },
}

# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("powerstroke_scrape.log"),
    ],
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────

session = requests.Session()
session.headers.update(HEADERS)

def _parse_cookie(cookie_str: str) -> dict:
    cookies = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies

def get_page(url: str, retries: int = 3) -> BeautifulSoup | None:
    cookies = _parse_cookie(COOKIE)
    for attempt in range(retries):
        try:
            time.sleep(DELAY + random.uniform(0.0, 0.5))
            r = session.get(url, cookies=cookies, timeout=20)
            if r.status_code == 200:
                return BeautifulSoup(r.text, "lxml")
            elif r.status_code == 403:
                log.warning(f"403 Forbidden: {url}")
                log.warning("Tip: set COOKIE_POWERSTROKE env var with your session cookie")
                return None
            elif r.status_code == 429:
                wait = 30 * (attempt + 1)
                log.warning(f"429 Rate limited — waiting {wait}s")
                time.sleep(wait)
            else:
                log.warning(f"HTTP {r.status_code}: {url}")
                return None
        except requests.RequestException as e:
            log.error(f"Request error ({attempt+1}/{retries}): {e}")
            time.sleep(5 * (attempt + 1))
    return None

# ─────────────────────────────────────────────────────────────
# XENFORO INDEX PARSING
# ─────────────────────────────────────────────────────────────

def get_thread_links(forum_url: str, flush_path: Path = None) -> list[dict]:
    """Walk all index pages and return list of thread metadata dicts."""
    threads = []
    # Request 50 threads per page (XenForo max) to cut index pages by ~3x
    sep = "&" if "?" in forum_url else "?"
    page_url = forum_url + sep + "per_page=50"
    page_num = 1

    while page_url:
        if MAX_PAGES and page_num > MAX_PAGES:
            log.info(f"Reached MAX_PAGES limit ({MAX_PAGES})")
            break

        log.info(f"  Index page {page_num}: {page_url}")
        soup = get_page(page_url)
        if not soup:
            break

        # XenForo 2 thread rows
        rows = (
            soup.select("div.structItem--thread") or
            soup.select("li.discussionListItem") or  # XF1 fallback
            soup.select(".structItem")
        )

        if not rows:
            log.warning(f"  No thread rows found on page {page_num} — stopping")
            break

        new_this_page = 0
        for row in rows:
            # Skip sticky/announcement rows if desired
            if "structItem--sticky" in row.get("class", []):
                pass  # include stickies — they're often valuable tech posts

            title_el = (
                row.select_one("div.structItem-title a[data-tp-primary]") or
                row.select_one("h3.structItem-title a[data-tp-primary]") or
                row.select_one("div.structItem-title a") or
                row.select_one("h3.structItem-title a") or
                row.select_one(".title a")
            )
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            href  = title_el.get("href", "")
            if not href or not title:
                continue

            full_url = urljoin(forum_url, href)

            # Reply / view counts from pairs
            dds = row.select(".pairs--justified dd")
            replies = dds[0].get_text(strip=True) if len(dds) > 0 else "?"
            views   = dds[1].get_text(strip=True) if len(dds) > 1 else "?"

            # Last post date
            last_post_el = row.select_one("time.structItem-latestDate")
            last_post = last_post_el.get("datetime", "")[:10] if last_post_el else ""

            # Author of thread
            author_el = row.select_one(".structItem-cell--meta .username")
            author = author_el.get_text(strip=True) if author_el else ""

            threads.append({
                "title":     title,
                "url":       full_url,
                "author":    author,
                "replies":   replies,
                "views":     views,
                "last_post": last_post,
            })
            new_this_page += 1

        log.info(f"  Found {new_this_page} threads on page {page_num} (total: {len(threads)})")

        # Flush to disk every 500 threads so you can watch progress
        if flush_path and len(threads) % 500 < new_this_page:
            with open(flush_path, "w", encoding="utf-8") as f:
                json.dump(threads, f, indent=2, ensure_ascii=False)
            log.info(f"  Flushed {len(threads)} threads → {flush_path.name}")

        if MAX_THREADS and len(threads) >= MAX_THREADS:
            log.info(f"Reached MAX_THREADS limit ({MAX_THREADS})")
            break

        # Next page link
        next_el = (
            soup.select_one("a.pageNav-jump--next") or
            soup.select_one("a[rel='next']") or
            soup.select_one(".PageNav a[rel='next']")
        )
        if next_el and next_el.get("href"):
            page_url = urljoin(forum_url, next_el["href"])
            page_num += 1
        else:
            log.info(f"  No next page found — index complete")
            break

    log.info(f"Total threads found in index: {len(threads)}")
    return threads

# ─────────────────────────────────────────────────────────────
# XENFORO THREAD CONTENT PARSING
# ─────────────────────────────────────────────────────────────

def scrape_thread(thread: dict) -> dict:
    """Fetch all pages of a thread and return posts list."""
    posts = []
    page_url = thread["url"]

    while page_url:
        soup = get_page(page_url)
        if not soup:
            break

        articles = (
            soup.select("article.message--post") or
            soup.select("article.message") or
            soup.select("li.message")  # XF1
        )

        for article in articles:
            # Author
            author_el = (
                article.select_one("h4.message-name span.username") or
                article.select_one("h4.message-name a") or
                article.select_one(".username strong") or
                article.select_one("a.username")
            )
            author = author_el.get_text(strip=True) if author_el else "Unknown"

            # Date/time
            date_el = article.select_one("time.u-dt")
            date = date_el.get("datetime", "")[:19].replace("T", " ") if date_el else ""

            # Post number / ID
            post_id = article.get("data-content", article.get("id", ""))

            # Body
            body_el = (
                article.select_one("div.bbWrapper") or
                article.select_one(".message-userContent .bbWrapper") or
                article.select_one(".messageText")
            )
            if not body_el:
                continue

            # Format quoted blocks
            for quote in body_el.select(".bbCodeBlock--quote, blockquote"):
                cite = quote.select_one(".bbCodeBlock-title")
                label = cite.get_text(strip=True) if cite else "Quote"
                content = quote.select_one(".bbCodeBlock-content")
                q_text = content.get_text(separator=" ", strip=True) if content else quote.get_text(separator=" ", strip=True)
                quote.replace_with(f"\n\n> **{label}**\n> {q_text}\n\n")

            # Remove spoiler/expand labels that add noise
            for el in body_el.select(".bbCodeBlock--spoiler, .bbCodeBlock-expandLink"):
                el.decompose()

            text = body_el.get_text(separator="\n", strip=True)
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = text.strip()

            if text and len(text) > 10:
                posts.append({
                    "post_id": post_id,
                    "author":  author,
                    "date":    date,
                    "text":    text,
                })

        # Next page within thread
        next_el = (
            soup.select_one("a.pageNav-jump--next") or
            soup.select_one("a[rel='next']")
        )
        if next_el and next_el.get("href"):
            page_url = urljoin(thread["url"], next_el["href"])
        else:
            break

    return {**thread, "posts": posts}

# ─────────────────────────────────────────────────────────────
# CHECKPOINT
# ─────────────────────────────────────────────────────────────

def load_checkpoint(output_dir: Path, name: str) -> set[str]:
    ckpt = output_dir / f"{name}.checkpoint.json"
    if ckpt.exists():
        with open(ckpt) as f:
            data = json.load(f)
        log.info(f"Loaded checkpoint: {len(data)} already-scraped URLs")
        return set(data)
    return set()

def save_checkpoint(output_dir: Path, name: str, scraped: set[str]):
    ckpt = output_dir / f"{name}.checkpoint.json"
    with open(ckpt, "w") as f:
        json.dump(sorted(scraped), f)

# ─────────────────────────────────────────────────────────────
# MARKDOWN OUTPUT
# ─────────────────────────────────────────────────────────────

def thread_to_md(thread: dict) -> str:
    lines = []
    lines.append(f"## {thread['title']}")
    lines.append(f"**URL:** {thread['url']}  ")
    lines.append(f"**Author:** {thread.get('author', '?')} | "
                 f"**Replies:** {thread.get('replies', '?')} | "
                 f"**Views:** {thread.get('views', '?')} | "
                 f"**Last Post:** {thread.get('last_post', '?')}  ")
    lines.append("")

    for i, post in enumerate(thread.get("posts", []), 1):
        date_str = f" — {post['date']}" if post.get("date") else ""
        lines.append(f"### Post {i} — {post['author']}{date_str}")
        lines.append("")
        lines.append(post["text"])
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)

def write_subforum_md(subforum: dict, threads: list[dict], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    fname = output_dir / f"powerstroke_{subforum['name']}.md"
    total_posts = sum(len(t.get("posts", [])) for t in threads)

    with open(fname, "w", encoding="utf-8") as f:
        f.write(f"# PowerStroke.org — {subforum['label']}\n")
        f.write(f"**Source:** {subforum['url']}  \n")
        f.write(f"**Scraped:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  \n")
        f.write(f"**Threads:** {len(threads)} | **Total Posts:** {total_posts}  \n")
        f.write("\n---\n\n")
        for t in threads:
            f.write(thread_to_md(t))
            f.write("\n\n")

    log.info(f"Wrote {len(threads)} threads ({total_posts} posts) → {fname}")
    return fname

def write_index_only_md(subforum: dict, threads: list[dict], output_dir: Path) -> Path:
    """Write just the thread index (no post content) — useful for --no-post-content mode."""
    output_dir.mkdir(parents=True, exist_ok=True)
    fname = output_dir / f"powerstroke_{subforum['name']}_INDEX.md"

    with open(fname, "w", encoding="utf-8") as f:
        f.write(f"# PowerStroke.org — {subforum['label']} — Thread Index\n")
        f.write(f"**Source:** {subforum['url']}  \n")
        f.write(f"**Scraped:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  \n")
        f.write(f"**Threads found:** {len(threads)}  \n\n")
        f.write("| # | Title | Author | Replies | Views | Last Post | URL |\n")
        f.write("|---|-------|--------|---------|-------|-----------|-----|\n")
        for i, t in enumerate(threads, 1):
            title = t['title'].replace("|", "\\|")
            f.write(f"| {i} | {title} | {t.get('author','?')} | "
                    f"{t.get('replies','?')} | {t.get('views','?')} | "
                    f"{t.get('last_post','?')} | {t['url']} |\n")

    log.info(f"Wrote index of {len(threads)} threads → {fname}")
    return fname

def write_combined_md(all_results: list[tuple], output_dir: Path) -> Path:
    fname = output_dir / "powerstroke_ALL_SUBFORUMS.md"
    total_threads = sum(len(t) for _, t in all_results)
    total_posts   = sum(len(p.get("posts", [])) for _, threads in all_results for p in threads)

    with open(fname, "w", encoding="utf-8") as f:
        f.write("# PowerStroke.org — All Subforums Combined\n")
        f.write(f"**Scraped:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  \n")
        f.write(f"**Total Threads:** {total_threads} | **Total Posts:** {total_posts}  \n\n")
        for sf, threads in all_results:
            f.write(f"\n\n# ══════════════════════════════════════\n")
            f.write(f"# {sf['label']}\n")
            f.write(f"# {sf['url']}\n")
            f.write(f"# Threads: {len(threads)}\n")
            f.write(f"# ══════════════════════════════════════\n\n")
            for t in threads:
                f.write(thread_to_md(t))
                f.write("\n\n")

    log.info(f"Wrote combined file → {fname}")
    return fname

def write_json_index(all_results: list[tuple], output_dir: Path) -> Path:
    index = []
    for sf, threads in all_results:
        for t in threads:
            index.append({
                "subforum": sf["name"],
                "title":    t.get("title"),
                "url":      t.get("url"),
                "author":   t.get("author"),
                "replies":  t.get("replies"),
                "views":    t.get("views"),
                "last_post":t.get("last_post"),
                "posts_scraped": len(t.get("posts", [])),
            })
    fname = output_dir / "powerstroke_thread_index.json"
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    log.info(f"Wrote JSON index → {fname} ({len(index)} entries)")
    return fname

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def run_subforum(sf_key: str, sf: dict, output_dir: Path, no_post_content: bool) -> tuple:
    log.info(f"\n{'='*60}")
    log.info(f"Subforum: {sf['label']}")
    log.info(f"URL:      {sf['url']}")
    log.info(f"{'='*60}")

    flush_path = output_dir / "powerstroke_thread_index.json"
    thread_list = get_thread_links(sf["url"], flush_path=flush_path)
    if MAX_THREADS:
        thread_list = thread_list[:MAX_THREADS]

    if no_post_content:
        write_index_only_md(sf, thread_list, output_dir)
        return sf, thread_list

    # Full scrape with post content
    done_urls = load_checkpoint(output_dir, sf["name"])
    scraped   = []
    skipped   = 0

    for i, thread in enumerate(thread_list, 1):
        if thread["url"] in done_urls:
            log.debug(f"  Skip (done): {thread['title'][:60]}")
            continue

        # Skip threads with no replies — just an unanswered original post
        replies = str(thread.get("replies", "0")).replace(",", "").strip()
        if replies.isdigit() and int(replies) == 0:
            log.debug(f"  Skip (no replies): {thread['title'][:60]}")
            done_urls.add(thread["url"])  # mark as done so we don't revisit
            skipped += 1
            continue

        log.info(f"  [{i}/{len(thread_list)}] {thread['title'][:70]}")
        full = scrape_thread(thread)
        scraped.append(full)
        done_urls.add(thread["url"])

        if len(scraped) % 25 == 0:
            save_checkpoint(output_dir, sf["name"], done_urls)
            # Append-write every 25 threads so progress isn't lost
            write_subforum_md(sf, scraped, output_dir)
            log.info(f"  Checkpoint: {len(scraped)} threads done")

    save_checkpoint(output_dir, sf["name"], done_urls)
    write_subforum_md(sf, scraped, output_dir)
    log.info(f"  Skipped {skipped} threads with no replies")
    return sf, scraped


def main():
    global MAX_PAGES, MAX_THREADS, DELAY

    parser = argparse.ArgumentParser(
        description="Scrape powerstroke.org — 99-03 7.3L subforums",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scrape_powerstroke.py
  python3 scrape_powerstroke.py --forums general problems
  python3 scrape_powerstroke.py --no-post-content
  python3 scrape_powerstroke.py --max-pages 20 --max-threads 200
  COOKIE_POWERSTROKE="xf_session=abc" python3 scrape_powerstroke.py

Available --forums keys:
  general  problems  interior  exterior  tech
        """
    )
    parser.add_argument("--forums", nargs="+", choices=list(SUBFORUMS.keys()),
                        default=list(SUBFORUMS.keys()),
                        help="Which subforums to scrape (default: all)")
    parser.add_argument("--max-pages",   type=int, default=None,
                        help="Max index pages per subforum")
    parser.add_argument("--max-threads", type=int, default=None,
                        help="Max threads per subforum")
    parser.add_argument("--delay", type=float, default=DELAY,
                        help=f"Seconds between requests (default: {DELAY})")
    parser.add_argument("--output-dir",  type=str, default=str(OUTPUT_DIR),
                        help="Output directory")
    parser.add_argument("--no-post-content", action="store_true",
                        help="Only scrape thread index (no post content)")
    parser.add_argument("--no-combined", action="store_true",
                        help="Skip writing the combined all-subforums file")
    args = parser.parse_args()

    if args.max_pages:   MAX_PAGES   = args.max_pages
    if args.max_threads: MAX_THREADS = args.max_threads
    DELAY = args.delay

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results = []
    for key in args.forums:
        sf = SUBFORUMS[key]
        result = run_subforum(key, sf, output_dir, args.no_post_content)
        all_results.append(result)

    if not args.no_combined and not args.no_post_content:
        write_combined_md(all_results, output_dir)

    write_json_index(all_results, output_dir)

    # Summary
    print(f"\n{'='*60}")
    print("POWERSTROKE.ORG SCRAPE COMPLETE")
    print(f"{'='*60}")
    for sf, threads in all_results:
        posts = sum(len(t.get("posts", [])) for t in threads)
        print(f"  {sf['label']:<45} {len(threads):>6} threads  {posts:>8} posts")
    print(f"\nOutput: {output_dir.resolve()}")


if __name__ == "__main__":
    main()

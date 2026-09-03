"""One-off crawler: pulls a site's text into the SQLite knowledge base for search_website().

Usage: python crawl.py <start_url> [--max-pages 30] [--max-depth 2]
"""

import argparse
import urllib.robotparser
from collections import deque
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import db

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
TIMEOUT = 10
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; concierge-bot-crawler/1.0)"}


def _allowed(rp: urllib.robotparser.RobotFileParser | None, url: str) -> bool:
    return rp is None or rp.can_fetch(HEADERS["User-Agent"], url)


def _extract_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return title, "\n".join(lines)


def _chunk(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append(text[start:end])
        start = end - CHUNK_OVERLAP
    return [c for c in chunks if c.strip()]


def _links(html: str, base_url: str, domain: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    found = []
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url, a["href"]).split("#")[0]
        if urlparse(url).netloc == domain and url.startswith("http"):
            found.append(url)
    return found


def run_crawl(start_url: str, max_pages: int = 30, max_depth: int = 2) -> dict:
    domain = urlparse(start_url).netloc
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.set_url(urljoin(start_url, "/robots.txt"))
        rp.read()
    except Exception:
        rp = None  # no robots.txt reachable - proceed without a disallow list

    seen = {start_url}
    queue = deque([(start_url, 0)])
    pages_done = 0
    chunks_done = 0

    while queue and pages_done < max_pages:
        url, depth = queue.popleft()
        if not _allowed(rp, url):
            continue
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"skip {url}: {e}")
            continue

        title, text = _extract_text(resp.text)
        chunks = _chunk(text)
        if chunks:
            page_id = db.upsert_kb_page(url, title)
            db.add_kb_chunks(page_id, url, chunks)
            chunks_done += len(chunks)
        pages_done += 1
        print(f"[{pages_done}/{max_pages}] {url} -> {len(chunks)} chunks")

        if depth < max_depth:
            for link in _links(resp.text, url, domain):
                if link not in seen:
                    seen.add(link)
                    queue.append((link, depth + 1))

    return {"pages": pages_done, "chunks": chunks_done}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("start_url")
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--max-depth", type=int, default=2)
    args = parser.parse_args()

    result = run_crawl(args.start_url, args.max_pages, args.max_depth)
    print(f"Done: {result['pages']} pages, {result['chunks']} chunks indexed.")

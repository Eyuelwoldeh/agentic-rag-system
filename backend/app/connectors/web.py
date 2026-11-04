"""Website crawler: breadth-first over same-site links from a start URL. No credentials needed."""
import ipaddress
import os
import socket
from collections import deque
from collections.abc import Iterator
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse

from .base import Doc, request

LABEL = "Website"
BLURB = "Search a public website, like your help center or product docs. No login needed."
OAUTH = None
PASTE = None
GUIDE = [
    "Enter the address of a public docs or help site. Only pages under that address are read.",
    "Pages are re-read on each sync. Internal/private network addresses are blocked for safety.",
]
CONFIG = [
    {"key": "start_url", "label": "Start URL", "help": "e.g. https://docs.example.com/ (only pages under this path are crawled)", "required": True},
    {"key": "max_pages", "label": "Max pages", "help": "Default 100."},
]
SKIP_TAGS = {"script", "style", "nav", "footer", "header", "noscript", "svg", "form"}
BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "section", "article", "pre"}


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text, self.links, self.title, self._skip, self._in_title = [], [], "", 0, False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "a" and (href := dict(attrs).get("href")):
            self.links.append(href)
        elif tag == "title":
            self._in_title = True
        if tag in BLOCK_TAGS:
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.text.append(data)


def parse(html: str) -> tuple[str, str, list[str]]:
    p = _Page()
    p.feed(html)
    lines = [" ".join(line.split()) for line in "".join(p.text).splitlines()]
    return p.title.strip(), "\n\n".join(line for line in lines if line), p.links


def _check_public(url: str) -> None:
    """SSRF guard: refuse to fetch private, loopback or link-local addresses."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError(f"Only http(s) URLs are allowed: {url}")
    if os.getenv("CRAWLER_ALLOW_PRIVATE") == "1":
        return
    for info in socket.getaddrinfo(u.hostname, None):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"Refusing to crawl non-public address {ip} ({u.hostname})")


def fetch(config: dict, since: datetime | None, creds: dict | None = None) -> Iterator[Doc]:
    start = (config.get("start_url") or "").strip()
    _check_public(start)
    root = urlparse(start)
    prefix = root.path.rsplit("/", 1)[0] + "/" if not root.path.endswith("/") else root.path
    limit = int(config.get("max_pages") or 100)
    seen, queue, fetched = {start}, deque([start]), 0
    # ponytail: always recrawls every page (no ETag/Last-Modified); fine for small doc sites
    while queue and fetched < limit:
        url = queue.popleft()
        try:
            _check_public(url)
            # redirects are not auto-followed: each hop must pass the SSRF check like any other link
            resp = request("GET", url, retries=2, follow_redirects=False, headers={"User-Agent": "agentic-rag-crawler/0.1"})
        except Exception:
            continue
        fetched += 1
        if resp.is_redirect:
            links, text, title = [resp.headers.get("location", "")], "", ""
        elif "text/html" not in resp.headers.get("content-type", ""):
            continue
        else:
            title, text, links = parse(resp.text)
        if text:
            yield Doc(external_id=url, title=title or url, text=text, url=url, updated_at=datetime.now(timezone.utc))
        for href in links:
            nxt = urldefrag(urljoin(url, href))[0]
            u = urlparse(nxt)
            if u.netloc == root.netloc and u.path.startswith(prefix) and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)

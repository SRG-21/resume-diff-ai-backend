"""
Import a job description from a public job posting URL.

Because the server fetches a user-supplied URL, requests are restricted to public
hosts (SSRF protection): every hop of a redirect chain is re-validated, private and
loopback addresses are refused, and the response size is capped.
"""
import asyncio
import html
import ipaddress
import json
import logging
import re
import socket
from html.parser import HTMLParser
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx

from config import settings

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 4
BLOCKED_SITES = ("linkedin.com", "indeed.com", "glassdoor.com")
USER_AGENT = "Mozilla/5.0 (compatible; ResumeDiffAI/1.0; +job-import)"


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        or addr.is_reserved or addr.is_unspecified
    )


async def validate_public_url(url: str) -> str:
    """Raise ValueError unless the URL is http(s) and resolves only to public addresses"""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Please enter a valid http(s) job posting link.")
    if parsed.port not in (None, 80, 443):
        raise ValueError("That link uses a port that is not allowed.")
    host = parsed.hostname
    try:
        # A literal IP skips DNS but is still checked below
        infos = await asyncio.get_running_loop().getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValueError("Could not find that website. Check the link and try again.")
    ips = {info[4][0] for info in infos}
    if not ips or not all(_is_public_ip(ip) for ip in ips):
        raise ValueError("That link is not allowed.")
    return parsed.geturl()


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "svg", "form", "iframe"}
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")
            if tag == "li":
                self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self.parts.append(data)


def html_to_text(markup: str) -> Tuple[str, str]:
    """Return (visible text, page title)"""
    parser = _TextExtractor()
    parser.feed(markup)
    text = "".join(parser.parts)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text, " ".join(parser.title.split())


def _find_job_posting(node) -> Optional[dict]:
    if isinstance(node, dict):
        kind = node.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            return node
        for value in node.values():
            found = _find_job_posting(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_job_posting(item)
            if found:
                return found
    return None


def parse_job_page(markup: str) -> dict:
    """Prefer structured JobPosting data (used by Greenhouse, Lever, Workable...) over scraping"""
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', markup, re.DOTALL | re.IGNORECASE):
        try:
            data = json.loads(html.unescape(block).strip())
        except ValueError:
            continue
        posting = _find_job_posting(data)
        if posting and posting.get("description"):
            description, _ = html_to_text(str(posting["description"]))
            org = posting.get("hiringOrganization")
            company = org.get("name") if isinstance(org, dict) else (org if isinstance(org, str) else None)
            return {"text": description, "title": posting.get("title"), "company": company}
    text, title = html_to_text(markup)
    return {"text": text, "title": title or None, "company": None}


async def fetch_job_text(url: str) -> dict:
    """Download and extract a job posting. Returns {"text", "title", "company", "url"}"""
    current = await validate_public_url(url)
    host = (urlparse(current).hostname or "").lower()
    blocked = any(host == d or host.endswith("." + d) for d in BLOCKED_SITES)

    body = b""
    async with httpx.AsyncClient(
        timeout=settings.JOB_IMPORT_TIMEOUT, follow_redirects=False, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                async with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("The job page redirected without a destination.")
                        current = await validate_public_url(urljoin(current, location))
                        continue
                    if response.status_code >= 400:
                        raise ValueError(
                            "That site blocked the request." if response.status_code in (401, 403, 429)
                            else f"The job page returned an error ({response.status_code})."
                        )
                    ctype = response.headers.get("content-type", "")
                    if "html" not in ctype and "text" not in ctype:
                        raise ValueError("That link does not look like a web page.")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > settings.JOB_IMPORT_MAX_BYTES:
                            raise ValueError("That page is too large to import.")
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    break
            except httpx.TimeoutException:
                raise ValueError("The job page took too long to load.")
            except httpx.HTTPError as e:
                logger.warning(f"Job import fetch failed: {e}")
                raise ValueError("Could not load that job page.")
        else:
            raise ValueError("The job page redirected too many times.")

    parsed = parse_job_page(body.decode("utf-8", errors="replace"))
    if len(parsed["text"]) < 200:
        if blocked:
            raise ValueError(
                "LinkedIn, Indeed and Glassdoor require sign-in, so their pages cannot be imported. "
                "Paste the job description instead, or use the browser extension."
            )
        raise ValueError("Could not find a job description on that page. Paste the text instead.")
    return {**parsed, "text": parsed["text"][: settings.MAX_TEXT_LENGTH], "url": current}

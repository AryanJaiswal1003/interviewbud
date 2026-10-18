"""Read a public job or application page.

Order: plain HTTP + main-content extraction (trafilatura); if that yields too little text (JS-rendered pages) and keys
are configured, Firecrawl (/v2/scrape, markdown) and then Browserless (/content, rendered HTML). Private, gated or
unreadable pages end in `needs_text`: we ask the user to paste the posting instead of guessing.

Server-side fetching of user-supplied URLs is an SSRF risk, so `safe_get` only connects to public addresses: it
resolves the host itself, refuses loopback/private/link-local/reserved ranges (cloud metadata included), pins the
connection to the checked IP (no DNS-rebinding window), and re-checks every redirect hop.
"""

import asyncio
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
import trafilatura

from ..config import get_settings

log = logging.getLogger(__name__)

MAX_BYTES = 3 * 1024 * 1024
MIN_TEXT = 400  # less than this after extraction usually means a JS shell or a gate
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36 InterviewBud/1.0")
GATE_WORDS = re.compile(r"\b(sign in|log in|login|create an account|join now|authwall|captcha|access denied|"
                        r"enable javascript)\b", re.I)


class FetchRefused(Exception):
    """The URL can't be fetched safely (bad scheme, non-public address)."""


@dataclass
class Page:
    status: str  # ok | needs_text
    method: str = ""
    final_url: str = ""
    title: str = ""
    text: str = ""
    reason: str = ""


def _public_ips(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise FetchRefused(f"Couldn't find the site {host}")
    ips = sorted({i[4][0] for i in infos})
    for ip in ips:
        addr = ipaddress.ip_address(ip.split("%")[0])
        if not addr.is_global or addr.is_multicast:
            raise FetchRefused("That address isn't on the public internet")
    return ips


def check_url(url: str) -> str:
    """Normalise and validate a user-supplied URL (scheme and shape only; addresses are checked at connect time)."""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise FetchRefused("Use an http or https link")
    if parts.username or parts.password:
        raise FetchRefused("Links with embedded credentials aren't supported")
    if parts.port not in (None, 80, 443, 8080, 8443):
        raise FetchRefused("That port isn't supported")
    try:  # literal IPs can be judged right away; hostnames are checked when we connect
        addr = ipaddress.ip_address(parts.hostname)
    except ValueError:
        addr = None
    if addr is not None and (not addr.is_global or addr.is_multicast):
        raise FetchRefused("That address isn't on the public internet")
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, ""))


async def safe_get(url: str, *, max_redirects: int = 5, timeout: float = 15.0) -> tuple[int, str, str, bytes]:
    """GET a public URL. Returns (status, final_url, content_type, body). Connects only to checked public IPs."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        for _ in range(max_redirects + 1):
            url = check_url(url)
            parts = urlsplit(url)
            port = parts.port or (443 if parts.scheme == "https" else 80)
            ips = await asyncio.to_thread(_public_ips, parts.hostname, port)
            ip = ips[0]
            host_for_url = f"[{ip}]" if ":" in ip else ip
            pinned = urlunsplit((parts.scheme, f"{host_for_url}:{port}", parts.path, parts.query, ""))
            headers = {"Host": parts.netloc, "User-Agent": UA, "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1",
                       "Accept-Language": "en"}
            async with client.stream("GET", pinned, headers=headers,
                                     extensions={"sni_hostname": parts.hostname}) as resp:
                if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                    url = urljoin(url, resp.headers["location"])
                    continue
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        break
                return resp.status_code, url, resp.headers.get("content-type", ""), bytes(body)
    raise FetchRefused("Too many redirects")


def _extract(html: str, url: str) -> tuple[str, str]:
    text = trafilatura.extract(html, url=url, include_tables=True, include_links=False, favor_recall=True,
                               output_format="markdown") or ""
    meta = trafilatura.extract_metadata(html)
    return (meta.title if meta and meta.title else ""), text.strip()


async def _firecrawl(url: str) -> Page | None:
    key = get_settings().firecrawl_api_key
    if not key:
        return None
    async with httpx.AsyncClient(timeout=70) as c:
        r = await c.post("https://api.firecrawl.dev/v2/scrape", headers={"Authorization": f"Bearer {key}"},
                         json={"url": url, "formats": ["markdown"], "onlyMainContent": True, "timeout": 60000})
    if r.status_code >= 400:
        log.warning("firecrawl %s: %s", r.status_code, r.text[:200])
        return None
    data = r.json().get("data") or {}
    meta = data.get("metadata") or {}
    title = meta.get("title")
    title = title[0] if isinstance(title, list) and title else (title or "")
    code = meta.get("statusCode") or 200
    text = (data.get("markdown") or "").strip()
    if code >= 400 or len(text) < MIN_TEXT:
        return None
    return Page("ok", "firecrawl", meta.get("url") or url, title, text)


async def _browserless(url: str) -> Page | None:
    s = get_settings()
    if not s.browserless_api_key:
        return None
    async with httpx.AsyncClient(timeout=70) as c:
        r = await c.post(f"{s.browserless_url.rstrip('/')}/content", params={"token": s.browserless_api_key},
                         json={"url": url, "gotoOptions": {"waitUntil": "networkidle2", "timeout": 45000}})
    if r.status_code >= 400:
        log.warning("browserless %s: %s", r.status_code, r.text[:200])
        return None
    title, text = _extract(r.text, url)
    return Page("ok", "browserless", url, title, text) if len(text) >= MIN_TEXT else None


async def read_page(url: str) -> Page:
    url = check_url(url)
    reason = ""
    try:
        status, final, ctype, body = await safe_get(url)
        if status in (401, 403, 407, 429, 451, 999):
            reason = "The page asked for a sign-in or blocked automated reading"
        elif status == 404 or status == 410:
            return Page("needs_text", reason="That page doesn't exist (it may have been taken down)")
        elif status >= 400:
            reason = f"The site answered with an error ({status})"
        else:
            html = body.decode("utf-8", errors="replace")
            if "text/plain" in ctype:
                title, text = "", html.strip()
            else:
                title, text = await asyncio.to_thread(_extract, html, final)
            if len(text) >= MIN_TEXT and not (len(text) < 1500 and GATE_WORDS.search(text)):
                return Page("ok", "http", final, title, text)
            reason = ("The page looks like a sign-in wall" if GATE_WORDS.search(text)
                      else "The page needs a browser to show its content")
    except FetchRefused:
        raise
    except (httpx.HTTPError, OSError) as e:
        reason = f"Couldn't reach the page ({type(e).__name__})"
    # Rendered fallbacks for JS-heavy pages (only if configured); they fetch from their own networks.
    for fallback in (_firecrawl, _browserless):
        try:
            page = await fallback(url)
        except httpx.HTTPError as e:
            log.warning("%s failed: %s", fallback.__name__, e)
            page = None
        if page:
            return page
    return Page("needs_text", reason=reason or "Couldn't read enough of the posting from that page")

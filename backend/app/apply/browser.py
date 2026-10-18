"""A real browser for reading and filling application forms (Playwright).

Production runs Chromium in its own container (`playwright run-server`, no access to the database or the Docker
socket) and this module drives it over a WebSocket; development launches a local headless Chromium.

Pages are untrusted. Every request the page makes passes `_guard`, which only lets it reach public internet
addresses (the same check as the job-page fetcher), so a hostile form can't use our browser to reach internal
services or cloud metadata. Service workers and downloads are off.
"""

import asyncio
import ipaddress
import logging
import re
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from playwright.async_api import Page, Route, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..config import get_settings
from ..prep import fetcher

log = logging.getLogger(__name__)
_sessions = asyncio.Semaphore(2)  # Chromium is memory-hungry; at most two forms at a time
CONFIRM_RE = re.compile(r"thank(s| you) for (applying|your (application|interest))|application (has been |was )?"
                        r"(received|submitted|sent)|we('ve| have) received your application|successfully submitted",
                        re.I)
CAPTCHA_RE = re.compile(r"verify (that )?you are (a )?human|are you a robot|complete the (security )?check", re.I)

READ_JS = r"""() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const textOf = (id) => id ? clean(id.split(/\s+/).map(i => document.getElementById(i)?.innerText || '').join(' ')) : '';
  const labelFor = (el) => {
    let t = '';
    if (el.labels && el.labels.length) t = el.labels[0].innerText;
    if (!t) t = textOf(el.getAttribute('aria-labelledby'));
    if (!t) t = el.getAttribute('aria-label') || '';
    if (!t) { const l = el.closest('label'); if (l) t = l.innerText; }
    if (!t) { const fs = el.closest('fieldset'); const lg = fs && fs.querySelector('legend'); if (lg) t = lg.innerText; }
    if (!t) t = el.getAttribute('placeholder') || '';
    return clean(t);
  };
  const sectionOf = (el) => {
    let n = el;
    for (let i = 0; i < 8 && n; i++, n = n.parentElement) {
      const h = n.querySelector && n.querySelector(':scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > legend');
      const t = h ? h.innerText : '';
      if (/voluntary|self[- ]identif|demographic|equal (employment )?opportunit|eeo/i.test(t)) return 'demographic';
    }
    return '';
  };
  const sel = (el) => el.id ? '#' + CSS.escape(el.id) : (el.name ? `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]` : '');
  const fields = [], seenRadio = new Set();
  const els = document.querySelectorAll('input, select, textarea, [role=combobox]');
  els.forEach((el, i) => {
    const type = (el.getAttribute('role') === 'combobox' ? 'combobox' : (el.tagName === 'SELECT' ? 'select' :
                 el.tagName === 'TEXTAREA' ? 'textarea' : (el.type || 'text'))).toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image', 'search'].includes(type)) return;
    if (/requiredInput/i.test(el.className || '') && !el.id) return;  // react-select's validation helper
    if (type === 'radio') {
      if (!el.name || seenRadio.has(el.name)) return;
      seenRadio.add(el.name);
      const group = [...document.querySelectorAll(`input[type=radio][name="${CSS.escape(el.name)}"]`)];
      const fs = el.closest('fieldset'), lg = fs && fs.querySelector('legend');
      fields.push({key: el.name, selector: `input[type=radio][name="${CSS.escape(el.name)}"]`, type: 'radio',
        label: clean(lg ? lg.innerText : labelFor(el)), options: group.map(r => labelFor(r)).filter(Boolean),
        required: group.some(r => r.required), section: sectionOf(el), name: el.name});
      return;
    }
    const s = sel(el);
    if (!s) return;
    const r = el.getBoundingClientRect();
    if (type !== 'file' && r.width === 0 && r.height === 0) return;
    let label = labelFor(el);
    const star = /\*\s*$/.test(label);
    label = label.replace(/\s*\*\s*$/, '');
    if (type === 'file' && /^(attach|upload|choose file)?$/i.test(label)) {
      // Greenhouse-style uploaders: the visible label sits on the group, not the input
      const grp = el.closest('[class*=upload], [class*=file], fieldset, .field, div');
      const head = grp && grp.parentElement && grp.parentElement.querySelector('label, legend, [class*=label]');
      label = clean(head ? head.innerText : (el.id || el.name)).replace(/\s*\*\s*$/, '');
    }
    const f = {key: el.id || el.name || `field${i}`, selector: s, type, label,
      required: el.required || el.getAttribute('aria-required') === 'true' || star,
      name: el.name || el.id || '', autocomplete: el.getAttribute('autocomplete') || '',
      description: textOf(el.getAttribute('aria-describedby')).slice(0, 300), section: sectionOf(el),
      maxlength: el.maxLength > 0 ? el.maxLength : null, accept: el.getAttribute('accept') || ''};
    if (type === 'select') f.options = [...el.options].map(o => clean(o.text)).filter(t => t && !/^(select|choose|please select|--)/i.test(t));
    fields.push(f);
  });
  const btns = [...document.querySelectorAll('button, input[type=submit]')];
  const submit = btns.find(b => /submit/i.test(b.innerText || b.value || '')) || btns.find(b => b.type === 'submit');
  return {fields, title: document.title, submit: submit ? clean(submit.innerText || submit.value) : ''};
}"""


async def _is_public(host: str, port: int, cache: dict) -> bool:
    if host in cache:
        return cache[host]
    try:
        ipaddress.ip_address(host.strip("[]"))
        literal = True
    except ValueError:
        literal = False
    try:
        if literal:
            addr = ipaddress.ip_address(host.strip("[]"))
            ok = addr.is_global and not addr.is_multicast
        else:
            await asyncio.to_thread(fetcher._public_ips, host, port)
            ok = True
    except fetcher.FetchRefused:
        ok = False
    cache[host] = ok
    return ok


@asynccontextmanager
async def open_page(url: str):
    """A fresh, isolated browser context on `url`. Yields the page; everything is torn down afterwards."""
    s = get_settings()
    allow_private = s.apply_allow_private
    cache: dict[str, bool] = {}

    async def guard(route: Route) -> None:
        parts = urlsplit(route.request.url)
        if parts.scheme in ("data", "blob"):
            await route.continue_()
            return
        if parts.scheme not in ("http", "https") or not parts.hostname:
            await route.abort("blockedbyclient")
            return
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if allow_private or await _is_public(parts.hostname, port, cache):
            await route.continue_()
        else:
            log.warning("application browser blocked a request to %s", parts.hostname)
            await route.abort("blockedbyclient")

    async with _sessions, async_playwright() as p:
        browser = await (p.chromium.connect(s.browser_ws_url, timeout=30000) if s.browser_ws_url
                         else p.chromium.launch())
        try:
            ctx = await browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US",
                                            service_workers="block", accept_downloads=False)
            await ctx.route("**/*", guard)
            page = await ctx.new_page()
            page.set_default_timeout(15000)
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                await page.wait_for_selector("form input, form textarea, input[type=email], [role=combobox]",
                                             timeout=20000)
            except PlaywrightTimeout:
                pass
            yield page
        finally:
            await browser.close()


async def _combobox_options(page: Page, selector: str) -> tuple[list[str], bool]:
    """Open a custom dropdown and read its options. Returns (options, is_search) where is_search means the list only
    appears after typing (e.g. a city autocomplete)."""
    loc = page.locator(selector).first
    try:
        await loc.scroll_into_view_if_needed()
        await loc.click()
        await page.wait_for_timeout(350)
        controls = await loc.get_attribute("aria-controls") or await loc.get_attribute("aria-owns")
        scope = page.locator(f"#{controls}") if controls else page.locator("[role=listbox]").last
        opts = [o.strip() for o in await scope.locator("[role=option]").all_inner_texts()]
        await page.keyboard.press("Escape")
        opts = [o for o in opts if o and not re.match(r"^(no options|loading|type to search)", o, re.I)]
        return opts[:300], not opts
    except (PlaywrightError, PlaywrightTimeout) as e:
        log.info("couldn't open dropdown %s: %s", selector, e)
        return [], True


async def read_form(url: str) -> dict:
    """The application form's fields as the candidate would see them: label, type, required, options."""
    async with open_page(url) as page:
        data = await page.evaluate(READ_JS)
        if not data["fields"]:
            # Some boards put the form behind an "Apply" button on the posting page.
            btn = page.get_by_role("link", name=re.compile(r"^apply", re.I)).or_(
                page.get_by_role("button", name=re.compile(r"^apply", re.I)))
            if await btn.count():
                await btn.first.click()
                await page.wait_for_timeout(2500)
                data = await page.evaluate(READ_JS)
        for f in data["fields"]:
            if f["type"] == "combobox":
                f["options"], f["search"] = await _combobox_options(page, f["selector"])
        data["final_url"] = page.url
        return data


async def _fill_one(page: Page, f: dict, value: str, files: dict) -> str:
    loc = page.locator(f["selector"]).first
    t = f["type"]
    if t == "file":
        blob = files.get(value)
        if not blob:
            return "no file"
        await loc.set_input_files({"name": blob["name"], "mimeType": blob["mime"], "buffer": blob["data"]})
        return "filled"
    if t == "select":
        await loc.select_option(label=value)
        return "filled"
    if t == "checkbox":
        if value.lower() in ("yes", "true", "checked", "on", "1"):
            await loc.check()
        return "filled"
    if t == "radio":
        for r in await page.locator(f["selector"]).all():
            rid = await r.get_attribute("id")
            text = (await page.locator(f"label[for='{rid}']").first.inner_text()) if rid else ""
            if text.strip().lower() == value.strip().lower():
                await r.check()
                return "filled"
        return "option not found"
    if t == "combobox":
        await loc.scroll_into_view_if_needed()
        await loc.click()
        await loc.fill(value[:80])
        await page.wait_for_timeout(1200 if f.get("search") else 400)
        opts = page.locator("[role=option]")
        exact = opts.filter(has_text=re.compile(rf"^\s*{re.escape(value.strip())}\s*$", re.I))
        target = exact if await exact.count() else opts.filter(has_text=re.compile(re.escape(value.strip()[:40]), re.I))
        if not await target.count():
            await page.keyboard.press("Escape")
            return "option not found"
        await target.first.click()
        return "filled"
    await loc.fill(value)
    return "filled"


async def fill(page: Page, fields: list[dict], values: dict[str, str], files: dict) -> dict[str, str]:
    report = {}
    for f in fields:
        v = values.get(f["key"], "")
        if not v:
            continue
        try:
            report[f["key"]] = await _fill_one(page, f, v, files)
        except (PlaywrightError, PlaywrightTimeout) as e:
            report[f["key"]] = f"failed: {str(e).splitlines()[0][:120]}"
    return report


async def fill_and_capture(url: str, fields: list[dict], values: dict[str, str], files: dict) -> dict:
    """Fill the form and take a full-page screenshot. Nothing is submitted."""
    async with open_page(url) as page:
        report = await fill(page, fields, values, files)
        await page.wait_for_timeout(500)
        shot = await page.screenshot(full_page=True, type="png")
        return {"report": report, "screenshot": shot, "final_url": page.url}


async def submit(url: str, fields: list[dict], values: dict[str, str], files: dict, button: str) -> dict:
    """Fill again and press the form's submit button. Stops (needs_you) at a human check rather than getting past
    it. Returns {outcome: submitted | needs_you | failed, detail, screenshot, report}."""
    async with open_page(url) as page:
        report = await fill(page, fields, values, files)
        failed = {k: v for k, v in report.items() if v != "filled"}
        if failed:
            return {"outcome": "failed", "detail": f"{len(failed)} fields couldn't be filled, so nothing was sent",
                    "report": report, "screenshot": await page.screenshot(full_page=True, type="png")}
        btn = page.get_by_role("button", name=re.compile(re.escape(button) if button else "submit", re.I)).or_(
            page.locator("button[type=submit], input[type=submit]"))
        if not await btn.count():
            return {"outcome": "needs_you", "detail": "We couldn't find the form's submit button", "report": report,
                    "screenshot": await page.screenshot(full_page=True, type="png")}
        before = page.url
        await btn.first.click()
        outcome, detail = "failed", "The page didn't confirm the application"
        for _ in range(30):
            await page.wait_for_timeout(1000)
            text = await page.locator("body").inner_text()
            if CONFIRM_RE.search(text):
                outcome, detail = "submitted", CONFIRM_RE.search(text).group(0)
                break
            challenge = await page.locator("iframe[src*='recaptcha/api2/bframe'], iframe[src*='hcaptcha'], "
                                           "iframe[title*='challenge' i]").count()
            if challenge or CAPTCHA_RE.search(text):
                outcome, detail = "needs_you", "The site asked for a human check; finish it yourself"
                break
            errors = await page.locator("[aria-invalid=true]").count()
            if errors and page.url == before:
                outcome, detail = "failed", f"The form reported {errors} problem(s) with the answers"
                break
        return {"outcome": outcome, "detail": detail, "report": report, "final_url": page.url,
                "screenshot": await page.screenshot(full_page=True, type="png")}

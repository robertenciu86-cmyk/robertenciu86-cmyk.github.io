#!/usr/bin/env python3
"""Generate the London Comedy Group website from Eventbrite.

Eventbrite is the editorial source of truth. The generated site contains:
- a conversion-focused homepage
- a show directory and durable recurring-show landing pages
- one Google-compatible leaf page per dated Eventbrite occurrence
- robots.txt and sitemap.xml

Run against live Eventbrite:
    EVENTBRITE_TOKEN=xxxx python3 scripts/update-events.py

Run deterministically without network access:
    python3 scripts/update-events.py \
        --fixture scripts/test-fixtures/events.json \
        --now 2026-05-31T13:00:00+01:00
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib import error, parse, request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ORG_ID = "1234539271983"
API = "https://www.eventbriteapi.com/v3"
ROOT = Path(__file__).resolve().parent.parent
OVERRIDES = ROOT / "scripts" / "event-overrides.json"
STATE = ROOT / "scripts" / "site-state.json"
MANIFEST = ROOT / "scripts" / "generated-files.json"
HEARTBEAT = ROOT / "scripts" / "refresh-heartbeat.txt"
BASE_URL = "https://londoncomedygroup.com"
OCCURRENCE_RETENTION_DAYS = 90
HEARTBEAT_INTERVAL_DAYS = 30
try:
    LONDON = ZoneInfo("Europe/London")
except ZoneInfoNotFoundError:
    # Minimal environments such as Termux may omit the system timezone database.
    # GitHub Actions has it; this fallback keeps local generation dependency-free.
    LONDON = dt.datetime.now().astimezone().tzinfo or dt.timezone.utc
ORG_NAME = "London Comedy Group"
ORG_EVENTBRITE = "https://www.eventbrite.co.uk/o/london-comedy-group-55764637993"
INSTAGRAM = "https://www.instagram.com/londoncomedygroup/"
TIKTOK = "https://www.tiktok.com/@londoncomedygroup1"
FACEBOOK = "https://www.facebook.com/profile.php?id=100089919127479"
BEEHIIV = "https://lcg-fans-broadcast.beehiiv.com/"
HIRE_FORM = "https://docs.google.com/forms/d/e/1FAIpQLSeAX08fLT3mb6UhwyncyRLHd-kmJPoai-x0kPE5TZ6V9kVJ6A/viewform?usp=header"
PERFORM_FORM = "https://docs.google.com/forms/d/e/1FAIpQLSe0maLGAuPg4ZUZVVOgpWob1n4oLKiT5mTJgfPZZ_o62k_tdg/viewform?usp=sharing&ouid=115747252269731556423"
# Paste your Google Analytics 4 Measurement ID here, e.g. "G-AB12CD34EF".
# Leave it empty to ship no analytics at all. See google-analytics-setup.txt
# (one folder up from this repo) for where to find this ID.
GA_MEASUREMENT_ID = "G-C2RBGG9435"
PUBLIC_SHOWS = {
    "freecomedyeverysundayat6pminislington": {
        "title": "Free Sunday Comedy in Islington: 6pm Show",
        "slug": "islington-sunday-comedy-6pm",
        "area": "Islington",
    },
    "candlemakercomedyfreecomedyatbatterseasfavourite": {
        "title": "Candlemaker Comedy in Battersea",
        "slug": "candlemaker-comedy-battersea",
        "area": "Battersea",
    },
    "freecomedyeverysundayat8pminislington": {
        "title": "Free Sunday Comedy in Islington: 8pm Show",
        "slug": "islington-sunday-comedy-8pm",
        "area": "Islington",
    },
    "isitlingtonfreecomedyeverytuesdayat730pminhighburyislington": {
        "title": "Is-It-Lington? Tuesday Comedy",
        "slug": "is-it-lington-tuesday-comedy",
        "area": "Highbury & Islington",
    },
    "byobfreecomedyeverythursdayinpeckham": {
        "title": "BYOB Comedy in Peckham",
        "slug": "byob-comedy-peckham",
        "area": "Peckham",
    },
    "comedyclubshoreditchfreecomedyeveryfridayineastlondonearly": {
        "title": "Comedy Club Shoreditch: Early Show",
        "slug": "comedy-club-shoreditch-early",
        "area": "Shoreditch",
    },
    "comedyclubshoreditchfreecomedyeveryfridayineastlondonfondue": {
        "title": "Comedy Club Shoreditch: Comedy + Fondue",
        "slug": "comedy-club-shoreditch-fondue",
        "area": "Shoreditch",
    },
    "londoncomedygroupsoho": {
        "title": "Saturday Comedy in Soho",
        "slug": "saturday-comedy-soho",
        "area": "Soho",
    },
}


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


_ASSET_VERSION: str | None = None


def asset_version() -> str:
    """Short content hash of the shared assets, appended to their URLs so a
    CSS/JS change always busts the browser cache instead of serving a stale copy."""
    global _ASSET_VERSION
    if _ASSET_VERSION is None:
        digest = hashlib.sha1()
        for name in ("assets/site.css", "assets/site.js"):
            path = ROOT / name
            if path.exists():
                digest.update(path.read_bytes())
        _ASSET_VERSION = digest.hexdigest()[:8]
    return _ASSET_VERSION


def analytics_snippet() -> str:
    """Google Analytics 4 with Consent Mode v2.

    Every storage type defaults to "denied", so gtag.js never writes a cookie
    until the visitor opts in. Returning visitors who already accepted have their
    choice replayed from localStorage *before* gtag init, so the very first
    page_view of the session is measured with cookies. Everyone else stays in
    cookieless (aggregated) mode until they accept via the banner (see site.js).
    Declining or ignoring the banner leaves analytics_storage denied, which under
    UK PECR / ePrivacy needs no consent because nothing is stored.

    Returns an empty string until GA_MEASUREMENT_ID is filled in, so the site
    ships clean with no analytics until you opt in."""
    if not GA_MEASUREMENT_ID:
        return ""
    return f"""
    <script async src="https://www.googletagmanager.com/gtag/js?id={esc(GA_MEASUREMENT_ID)}"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){{dataLayer.push(arguments);}}
      gtag('consent', 'default', {{
        'ad_storage': 'denied',
        'ad_user_data': 'denied',
        'ad_personalization': 'denied',
        'analytics_storage': 'denied'
      }});
      try {{
        if (localStorage.getItem('lcg-analytics-consent') === 'granted') {{
          gtag('consent', 'update', {{ 'analytics_storage': 'granted' }});
        }}
      }} catch (e) {{}}
      gtag('js', new Date());
      gtag('config', '{esc(GA_MEASUREMENT_ID)}');
    </script>"""


def consent_banner() -> str:
    """Cookie-consent banner for the analytics opt-in.

    Ships hidden (the `hidden` attribute); site.js reveals it only when the
    visitor has not yet made a choice, so returning visitors never see it again.
    Accepting flips analytics_storage to granted and remembers the choice; see
    site.js. Returns empty when analytics is off, so there is nothing to consent
    to and no banner."""
    if not GA_MEASUREMENT_ID:
        return ""
    return """<div class="consent-banner" id="consent-banner" role="dialog" aria-live="polite" aria-label="Cookie consent" hidden>
        <p class="consent-text">We use cookies to measure how the site is used. You can accept analytics cookies or keep browsing without them.</p>
        <div class="consent-actions">
            <button class="button button-ghost" type="button" data-consent="denied">Decline</button>
            <button class="button button-primary" type="button" data-consent="granted">Accept analytics</button>
        </div>
    </div>"""


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    write_text(path, text)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.write_text(text, encoding="utf-8")


# Eventbrite occasionally returns a transient 403/429/5xx; the scheduled run
# should ride those out rather than emailing a failure that self-heals 6h later.
RETRY_STATUSES = {403, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4


def api_get(path: str, params: dict) -> dict:
    token = os.environ.get("EVENTBRITE_TOKEN")
    if not token:
        raise RuntimeError("Set EVENTBRITE_TOKEN or use --fixture.")
    qs = parse.urlencode(params)
    req = request.Request(
        f"{API}{path}?{qs}", headers={"Authorization": f"Bearer {token}"}
    )
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except error.HTTPError as ex:
            if ex.code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                raise
            reason: object = ex.code
        except error.URLError as ex:
            if attempt == MAX_ATTEMPTS:
                raise
            reason = ex.reason
        delay = 2 ** attempt
        print(
            f"Eventbrite API transient error ({reason}); retrying in {delay}s "
            f"(attempt {attempt}/{MAX_ATTEMPTS - 1}).",
            file=sys.stderr,
        )
        time.sleep(delay)
    raise RuntimeError("Eventbrite API retries exhausted.")


def fetch_events(status: str) -> list[dict]:
    events: list[dict] = []
    page = 1
    while True:
        data = api_get(
            f"/organizations/{ORG_ID}/events/",
            {
                "status": status,
                "order_by": "start_asc",
                "expand": "venue,ticket_availability",
                "page_size": 50,
                "page": page,
            },
        )
        events.extend(data.get("events", []))
        pagination = data.get("pagination", {})
        if not pagination.get("has_more_items"):
            return events
        page += 1


def load_events(fixture: str | None) -> tuple[list[dict], list[dict]]:
    if fixture:
        data = load_json(Path(fixture), {})
        return data.get("events", []), data.get("cancelled_events", [])
    live = fetch_events("live")
    try:
        cancelled = fetch_events("canceled")
    except error.HTTPError as ex:
        # A live refresh must never fail because cancellation history is unavailable.
        print(f"Warning: could not fetch cancelled events ({ex.code}).", file=sys.stderr)
        cancelled = []
    return live, cancelled


def parse_local(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LONDON)
    return parsed


def event_start(event: dict) -> dt.datetime:
    return parse_local(event["start"]["local"])


def event_end(event: dict) -> dt.datetime:
    end = (event.get("end") or {}).get("local")
    return parse_local(end) if end else event_start(event) + dt.timedelta(hours=2)


def series_key(event: dict) -> str:
    return str(event.get("series_id") or event["id"])


def normalized_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return slug[:72].rstrip("-") or "comedy-show"


def venue(event: dict) -> dict:
    return event.get("venue") or {}


def address(event: dict) -> dict:
    return venue(event).get("address") or {}


def venue_name(event: dict) -> str:
    return venue(event).get("name") or "London venue"


def address_display(event: dict) -> str:
    data = address(event)
    return data.get("localized_address_display") or ", ".join(
        value
        for value in (
            data.get("address_1"),
            data.get("city"),
            data.get("postal_code"),
        )
        if value
    )


def occurrence_key(event: dict) -> tuple[str, str, str]:
    place = address_display(event) or venue_name(event)
    return series_key(event), normalized_text(place), event["start"]["local"]


def dedupe_occurrences(events: list[dict]) -> list[dict]:
    seen: dict[tuple[str, str], dict] = {}
    for event in events:
        key = occurrence_key(event)
        if key not in seen or str(event["id"]) < str(seen[key]["id"]):
            seen[key] = event
    return list(seen.values())


def event_title(event: dict) -> str:
    return (event.get("name") or {}).get("text") or "Stand-up comedy in London"


def public_details(event: dict) -> dict:
    return PUBLIC_SHOWS.get(normalized_text(event_title(event)), {})


def public_title(event: dict) -> str:
    return public_details(event).get("title") or event_title(event)


def public_area(event: dict) -> str:
    return public_details(event).get("area") or "London"


def event_summary(event: dict) -> str:
    summary = (event.get("summary") or "").strip()
    if summary:
        return summary
    return f"Live stand-up comedy at {venue_name(event)} in London."


def best_image(event: dict) -> str:
    logo = event.get("logo") or {}
    return ((logo.get("original") or {}).get("url") or logo.get("url") or
            f"{BASE_URL}/london-comedy-group-logo.jpg")


def ticket_data(event: dict) -> tuple[str, str, bool]:
    availability = event.get("ticket_availability") or {}
    minimum = availability.get("minimum_ticket_price") or {}
    maximum = availability.get("maximum_ticket_price") or {}
    low = minimum.get("major_value")
    high = maximum.get("major_value")
    sold_out = bool(availability.get("is_sold_out"))
    if low is None:
        return "Check Eventbrite", "", sold_out

    def money(value: str) -> str:
        amount = float(value)
        return f"£{int(amount)}" if amount == int(amount) else f"£{amount:.2f}"

    if float(low) == 0 and (high is None or float(high) == 0):
        return "Free entry", "0", sold_out
    if high is not None and float(high) != float(low):
        return f"{money(low)} - {money(high)}", str(low), sold_out
    return money(low), str(low), sold_out


def tracked_url(url: str, campaign: str, content: str) -> str:
    parsed = parse.urlsplit(url)
    query = dict(parse.parse_qsl(parsed.query, keep_blank_values=True))
    query.update(
        {
            "utm_source": "londoncomedygroup.com",
            "utm_medium": "website",
            "utm_campaign": campaign,
            "utm_content": content,
        }
    )
    return parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, parse.urlencode(query), parsed.fragment)
    )


def format_day(value: dt.datetime) -> str:
    return f"{value.strftime('%A')} {value.day} {value.strftime('%B')}"


def format_full_date(value: dt.datetime) -> str:
    return f"{value.strftime('%A')} {value.day} {value.strftime('%B %Y')}"


def format_time(value: dt.datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def iso_with_zone(value: dt.datetime) -> str:
    return value.isoformat()


def json_script(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f'<script type="application/ld+json">{payload}</script>'


def meta_description(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= 156:
        return text
    return text[:157].rsplit(" ", 1)[0].rstrip(" ,.;:-")


def absolute(path: str) -> str:
    return BASE_URL + (path if path.startswith("/") else f"/{path}")


def list_text(values: list[str]) -> str:
    values = list(dict.fromkeys(value for value in values if value))
    if not values:
        return "London"
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def layout(
    *,
    title: str,
    description: str,
    canonical: str,
    body: str,
    now: dt.datetime,
    image: str | None = None,
    json_ld: list[dict] | None = None,
    robots: str | None = None,
) -> str:
    canonical_url = absolute(canonical)
    social_image = image or f"{BASE_URL}/london-comedy-group-logo.jpg"
    robot_tag = f'\n    <meta name="robots" content="{esc(robots)}">' if robots else ""
    structured = "\n    ".join(json_script(item) for item in (json_ld or []))
    if structured:
        structured = "\n    " + structured
    return f"""<!doctype html>
<html lang="en-GB">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="theme-color" content="#090811">
    <title>{esc(title)}</title>
    <meta name="description" content="{esc(meta_description(description))}">
    <link rel="canonical" href="{esc(canonical_url)}">{robot_tag}
    <meta property="og:type" content="website">
    <meta property="og:title" content="{esc(title)}">
    <meta property="og:description" content="{esc(meta_description(description))}">
    <meta property="og:url" content="{esc(canonical_url)}">
    <meta property="og:image" content="{esc(social_image)}">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{esc(title)}">
    <meta name="twitter:description" content="{esc(meta_description(description))}">
    <meta name="twitter:image" content="{esc(social_image)}">
    <link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
    <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
    <link rel="manifest" href="/site.webmanifest">
    <link rel="preconnect" href="https://img.evbuc.com">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700;12..96,800&display=swap">
    <link rel="stylesheet" href="/assets/site.css?v={asset_version()}">{structured}{analytics_snippet()}
</head>
<body>
    <a class="skip-link" href="#main">Skip to content</a>
    <header class="site-header">
        <div class="wrap nav">
            <a class="brand" href="/" aria-label="London Comedy Group home">
                <img src="/london-comedy-group-logo.jpg" width="52" height="52" alt="">
                <span>London Comedy Group</span>
            </a>
            <button class="nav-toggle" type="button" aria-expanded="false" aria-controls="site-nav">Menu</button>
            <nav id="site-nav" class="nav-links" aria-label="Main navigation">
                <a href="/#shows">This week</a>
                <a href="/shows/">All shows</a>
                <a href="/#comedians">Comedians</a>
                <a href="/stay-in-touch/">Stay in touch</a>
            </nav>
        </div>
    </header>
    <main id="main">{body}</main>
    <footer class="site-footer">
        <div class="wrap footer-grid">
            <div><strong>London Comedy Group</strong><p>Live stand-up comedy across London.</p></div>
            <div class="footer-links">
                <a href="/shows/">Find a show</a>
                <a href="/stay-in-touch/">Mailing list</a>
                <a href="/hire-comedians-london/">Hire comedians</a>
                <a href="/perform-with-us/">Perform with us</a>
                <a href="{esc(INSTAGRAM)}" rel="noopener noreferrer" target="_blank">Instagram</a>
                <a href="{esc(TIKTOK)}" rel="noopener noreferrer" target="_blank">TikTok</a>
                <a href="{esc(FACEBOOK)}" rel="noopener noreferrer" target="_blank">Facebook</a>
            </div>
        </div>
        <div class="wrap footer-bottom">© {now.year} London Comedy Group</div>
    </footer>
    {consent_banner()}
    <script src="/assets/site.js?v={asset_version()}" defer></script>
</body>
</html>
"""


def show_slug(series: str, event: dict, state: dict, used: set[str]) -> str:
    entry = state["series"].get(series)
    if entry and entry.get("slug"):
        used.add(entry["slug"])
        return entry["slug"]
    base = public_details(event).get("slug") or slugify(public_title(event))
    slug = base
    if slug in used:
        slug = f"{base}-{series[-6:]}"
    used.add(slug)
    return slug


def update_state(live_events: list[dict], state: dict, today: str) -> dict[str, str]:
    state.setdefault("series", {})
    used = {item["slug"] for item in state["series"].values() if item.get("slug")}
    live_keys = {series_key(event) for event in live_events}
    slugs: dict[str, str] = {}
    for event in sorted(live_events, key=event_start):
        key = series_key(event)
        if key not in state["series"]:
            # Adopt a previously-seen show's durable URL when Eventbrite changes
            # its series identifier or when the initial fixture is replaced by
            # the first live API refresh.
            for old_key, old_entry in list(state["series"].items()):
                source_title = old_entry.get("source_title") or old_entry.get("title", "")
                same_title = normalized_text(source_title) == normalized_text(event_title(event))
                same_venue = normalized_text(old_entry.get("venue_name", "")) == normalized_text(venue_name(event))
                if old_key not in live_keys and same_title and same_venue:
                    state["series"][key] = state["series"].pop(old_key)
                    break
        slug = show_slug(key, event, state, used)
        slugs[key] = slug
        state["series"][key] = {
            "slug": slug,
            "title": public_title(event),
            "source_title": event_title(event),
            "summary": event_summary(event),
            "image": best_image(event),
            "venue_name": venue_name(event),
            "address": address(event),
            "last_seen": today,
        }
    return slugs


def group_by_series(events: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for event in sorted(events, key=event_start):
        grouped.setdefault(series_key(event), []).append(event)
    return grouped


def annotate_event_path(event: dict, slug: str, occurrences: dict) -> str:
    previous = occurrences.get(str(event["id"]), {})
    path = previous.get("path") or event_path(event, slug)
    event["_site_path"] = path
    return path


def retention_deadline(event: dict) -> dt.datetime:
    return event_end(event) + dt.timedelta(days=OCCURRENCE_RETENTION_DAYS)


def update_occurrences(
    live: list[dict],
    cancelled: list[dict],
    state: dict,
    slugs: dict[str, str],
    now: dt.datetime,
) -> dict[str, dict]:
    occurrences = state.setdefault("occurrences", {})
    observed: set[str] = set()
    today = now.date().isoformat()

    for event in live:
        event_id = str(event["id"])
        annotate_event_path(event, slugs[series_key(event)], occurrences)
        occurrences[event_id] = {
            "event": public_event(event),
            "last_seen": today,
            "path": event_path(event, slugs[series_key(event)]),
            "status": "live",
        }
        observed.add(event_id)

    for event in cancelled:
        event_id = str(event["id"])
        key = series_key(event)
        entry = state["series"].get(key)
        if not entry:
            continue
        annotate_event_path(event, entry["slug"], occurrences)
        occurrences[event_id] = {
            "event": public_event(event),
            "last_seen": today,
            "path": event_path(event, entry["slug"]),
            "status": "cancelled",
        }
        observed.add(event_id)

    for event_id, item in list(occurrences.items()):
        event = item.get("event") or {}
        if event_id not in observed and item.get("status") == "live":
            item["status"] = "expired"
        if not event or retention_deadline(event) < now:
            occurrences.pop(event_id)

    return occurrences


def event_path(event: dict, slug: str) -> str:
    if event.get("_site_path"):
        return event["_site_path"]
    stamp = event_start(event).strftime("%Y-%m-%d-%H%M")
    return f"/events/{slug}-{stamp}-{event['id']}/"


def public_event(event: dict) -> dict:
    return {key: value for key, value in event.items() if not key.startswith("_")}


def preferred_event(events: list[dict]) -> dict:
    return next((event for event in events if not ticket_data(event)[2]), events[0])


def breadcrumb(items: list[tuple[str, str]]) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": pos, "name": name, "item": absolute(path)}
            for pos, (name, path) in enumerate(items, 1)
        ],
    }


def ticket_button(event: dict, slug: str, content: str, label: str | None = None) -> str:
    price, _, sold_out = ticket_data(event)
    url = tracked_url(event["url"], slug, content)
    text = label or ("View sold-out event" if sold_out else ("Get free tickets" if price == "Free entry" else "Get tickets"))
    return (
        f'<a class="button button-primary" data-ticket-link data-show="{esc(slug)}" '
        f'data-placement="{esc(content)}" href="{esc(url)}" rel="noopener noreferrer" '
        f'target="_blank">{esc(text)}</a>'
    )


def event_card(event: dict, slug: str, placement: str) -> str:
    start = event_start(event)
    price, _, sold_out = ticket_data(event)
    status = '<span class="pill pill-muted">Sold out</span>' if sold_out else ""
    return f"""
        <article class="event-card">
            <a class="card-image-link" href="{esc(event_path(event, slug))}">
                <img class="card-image" src="{esc(best_image(event))}" width="640" height="360"
                     loading="lazy" alt="{esc(public_title(event))}">
            </a>
            <div class="card-body">
                <div class="pill-row"><span class="pill">{esc(format_day(start))}</span>{status}</div>
                <h3><a href="{esc(event_path(event, slug))}">{esc(public_title(event))}</a></h3>
                <p class="card-meta"><strong>{esc(format_time(start))}</strong> · <span class="price{' price-free' if price == 'Free entry' else ''}">{esc(price)}</span></p>
                <p class="card-meta">{esc(venue_name(event))}<br>{esc(address_display(event))}</p>
                <div class="card-actions">
                    {ticket_button(event, slug, placement)}
                    <a class="text-link" href="{esc(event_path(event, slug))}">Details</a>
                </div>
            </div>
        </article>"""


def organizer_schema() -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": ORG_NAME,
        "url": BASE_URL,
        "logo": f"{BASE_URL}/london-comedy-group-logo.jpg",
        "sameAs": [ORG_EVENTBRITE, INSTAGRAM, TIKTOK, FACEBOOK],
    }


def render_comedians() -> str:
    runners = [
        {
            "name": "Ridwan Hussain",
            "image": "/ridwan-hussain.jpg",
            "bio": 'An exciting comedian with quick wit and relatable material. "Delightfully Funny" - Chortle.',
            "highlights": [
                "Star of Bound & Gagged's AAA Stand-Up Show",
                "Winner: Crack Comedy New Comedian",
                "Regular at Backyard Comedy Club, Up the Creek, Big Belly, and The Comedy Store",
            ],
            # NOT a typo / wrong link — this is deliberate. The bit is that
            # Ridwan (an Indian comic) runs his Instagram under a deadpan
            # "white guy" stage name, Richard Hudson. Leave the handle as-is.
            "instagram": "https://www.instagram.com/richardhudsoncomedy/",
        },
        {
            "name": "Robert Enciu",
            "image": "/robert-enciu.jpg",
            "bio": "A rising voice on London's comedy circuit, bringing an Eastern European perspective, sharp observations, and an easy stage presence.",
            "highlights": [
                "Romanian-British cultural comedy",
                "Material about UK and Eastern European life",
                "Comedy clips watched online",
            ],
            "instagram": "https://www.instagram.com/robert_out_here_/",
        },
        {
            "name": "Brendan Morris",
            "image": "/brendan-morris.jpg",
            "bio": "Irish comedian based in East London. An actor by trade, Brendan brings irreverent humour, spontaneous energy, and character-comedy bits to the stage.",
            "highlights": [
                "MC and host at London comedy nights",
                "Host of Is-It-Lington comedy at Hoxley & Porter",
                "Actor with a natural, spontaneous performance style",
            ],
            "instagram": "https://www.instagram.com/vagabondbrendan/",
        },
    ]
    regulars = [
        ("Peter Jones", "/peter_jones.jpg", "Regular performer"),
        ("Mike Rice", "/mike_rice.jpeg", "Regular performer"),
        ("Evaldas Karosas", "/evaldas_karosas.jpg", "Regular performer"),
        ("Shalaka Kurup", "/shalaka_kurup.jpeg", "Regular performer"),
        ("Dominic Fraser", "/dominic_fraser.jpg", "Host - Craft Comedy"),
        ("Anno Gomes", "/anno_gomes.jpg", "Regular performer"),
        ("Oliver Moore", "/oliver_moore.jpg", "Sporadic performer"),
        ("John Sharp", "/john_sharp.jpg", "Regular performer"),
        ("Samma", "/samma.jpg", "Regular performer"),
        ("Donatas", "/donatas.jpeg", "Regular performer"),
        ("Ramsey Smith", "/ramsey_smith.jpg", "Regular performer"),
        ("Thomas Noack", "/thomas_noack.jpg", "Regular performer"),
    ]
    runner_cards = "".join(
        f"""<article class="comedian-card">
            <img class="comedian-portrait" src="{esc(runner["image"])}" width="180" height="180" loading="lazy" alt="{esc(runner["name"])}">
            <h3>{esc(runner["name"])}</h3>
            <p>{esc(runner["bio"])}</p>
            <ul>{"".join(f"<li>{esc(item)}</li>" for item in runner["highlights"])}</ul>
            <a class="text-link" href="{esc(runner["instagram"])}" rel="noopener noreferrer" target="_blank">Follow on Instagram</a>
        </article>"""
        for runner in runners
    )
    regular_cards = "".join(
        f"""<article class="act-card">
            <img src="{esc(image)}" width="112" height="112" loading="lazy" alt="{esc(name)}">
            <strong>{esc(name)}</strong><span>{esc(role)}</span>
        </article>"""
        for name, image, role in regulars
    )
    return f"""
    <section id="comedians" class="section wrap">
        <div class="section-heading"><p class="eyebrow">The people behind the nights</p><h2>Meet the show runners</h2>
            <p>London Comedy Group nights are built and hosted by working comics, with regular guest acts from across the circuit.</p></div>
        <div class="comedian-grid">{runner_cards}</div>
        <div class="roster-block"><h2>Some of our acts</h2><div class="act-grid">{regular_cards}</div></div>
    </section>
    <section class="section section-alt">
        <div class="wrap"><div class="section-heading"><p class="eyebrow">From the rooms</p><h2>Comedy night gallery</h2></div>
        <div class="gallery-grid">
            <img src="/comedy-poster.jpg" width="600" height="300" loading="lazy" alt="London Comedy Group show poster">
            <img src="/comedy-venue.jpg" width="600" height="300" loading="lazy" alt="Bistrot Walluc comedy venue interior">
            <img src="/comedy-audience.jpg" width="600" height="300" loading="lazy" alt="Audience at a London Comedy Group night">
        </div></div>
    </section>"""


HOME_FAQ = [
    (
        "Is the comedy really free?",
        "Yes. Most London Comedy Group nights are free to attend — you just reserve a free ticket on Eventbrite so we can hold your seat. A few special shows, such as our Soho night, are a few pounds, and the price is always shown on the show page before you book.",
    ),
    (
        "Do I need to book in advance?",
        "Booking is free and takes under a minute. Our rooms regularly fill up, so reserving a ticket is the surest way to get in — walk-ups are welcome only if there is space left on the night.",
    ),
    (
        "Where are the shows?",
        "We run weekly stand-up nights across London, including Islington, Highbury, Battersea, Peckham, Shoreditch, and Soho. Every show page lists the exact venue address with a link to open it in Google Maps.",
    ),
    (
        "What time do shows start and how long do they last?",
        "Most shows start in the early evening and run for about two hours. Check the show page for the exact start time and arrive a little early to get a good seat.",
    ),
    (
        "Can I come on my own, and is there food and drink?",
        "Plenty of people come solo or bring a group — either is welcome. Most of our venues serve food and drinks, and a couple are bring-your-own, so it makes an easy night out.",
    ),
]


def render_faq() -> str:
    items = "".join(
        f"""<details class="faq-item">
            <summary><span>{esc(question)}</span></summary>
            <p>{esc(answer)}</p>
        </details>"""
        for question, answer in HOME_FAQ
    )
    return f"""
    <section id="faq" class="section wrap narrow">
        <div class="section-heading"><p class="eyebrow">Good to know</p><h2>Coming to a comedy show</h2>
            <p>Everything you need before your first London Comedy Group night.</p></div>
        <div class="faq-list">{items}</div>
    </section>"""


def faq_schema() -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {"@type": "Answer", "text": answer},
            }
            for question, answer in HOME_FAQ
        ],
    }


def render_home(active: dict[str, list[dict]], slugs: dict[str, str], now: dt.datetime) -> str:
    next_events = [preferred_event(events) for events in active.values()]
    next_events.sort(key=event_start)
    cards = "".join(event_card(event, slugs[series_key(event)], "homepage-card") for event in next_events) or """
        <div class="empty-state"><h3>New comedy dates are on the way</h3>
            <p>There are no live Eventbrite listings right now. Join the mailing list to hear when the next London shows open.</p>
            <a class="button button-primary" href="/stay-in-touch/">Join the mailing list</a></div>"""
    areas = "".join(
        f"""<a class="area-card" href="/shows/{esc(slugs[key])}/">
                <span>{esc(venue_name(preferred_event(events)))}</span>
                <strong>{esc(public_title(preferred_event(events)))}</strong>
                <small>{esc(format_day(event_start(preferred_event(events))))} · {esc(format_time(event_start(preferred_event(events))))}</small>
            </a>"""
        for key, events in sorted(active.items(), key=lambda item: event_start(preferred_event(item[1])))
    )
    area_names = list_text([public_area(event) for event in next_events])
    weekly_count = len(next_events)
    area_count = len({public_area(event) for event in next_events})
    hero_copy = (
        f"Most of our shows are free. Pick a night in {area_names}, reserve a seat in under a minute, and turn up ready to laugh."
        if next_events
        else "Fresh London stand-up dates land here automatically the moment booking opens on Eventbrite. Join the mailing list so you hear first."
    )
    directory_copy = (
        f"{weekly_count} stand-up nights on across London this week. Most are free — pick one and grab your seat."
        if next_events
        else "There are no live listings today. New Eventbrite dates will appear here automatically."
    )
    trust_items = (
        [f"{weekly_count} shows this week", "Free entry at most nights", f"Across {area_count} London areas"]
        if next_events
        else ["Weekly shows", "London venues", "Free and low-cost tickets"]
    )
    trust_row = "".join(f"<span>{esc(item)}</span>" for item in trust_items)
    body = f"""
    <section class="hero">
        <div class="wrap hero-grid">
            <div>
                <p class="eyebrow">Live stand-up across London</p>
                <h1>Free comedy in London, almost every night</h1>
                <p class="hero-copy">{esc(hero_copy)}</p>
                <div class="hero-actions">
                    <a class="button button-primary" href="#shows">See this week's shows</a>
                    <a class="button button-ghost" href="/shows/">Browse all comedy nights</a>
                </div>
                <div class="trust-row">{trust_row}</div>
            </div>
            <img class="hero-image" src="/comedy-audience.jpg" width="600" height="300" fetchpriority="high"
                 alt="Audience laughing at a London Comedy Group stand-up show">
        </div>
    </section>
    <section id="shows" class="section wrap">
        <div class="section-heading">
            <p class="eyebrow">Book your next night out</p>
            <h2>Upcoming comedy shows</h2>
            <p>{esc(directory_copy)}</p>
        </div>
        <div class="event-grid">{cards}</div>
        <div class="center"><a class="button button-ghost" href="/shows/">See all comedy nights</a></div>
    </section>
    <section class="section section-alt">
        <div class="wrap">
            <div class="section-heading"><p class="eyebrow">Find your local night</p><h2>Comedy around London</h2></div>
            <div class="area-grid">{areas or '<p>New venue dates will appear here automatically.</p>'}</div>
        </div>
    </section>
    <section class="section wrap split">
        <div><p class="eyebrow">A simple night out</p><h2>Booked in three steps</h2>
        <p>No membership, no faff. Find a night that suits you, reserve a free seat, and show up ready to laugh.</p></div>
        <div class="feature-list"><div><strong>1 · Pick a show</strong><span>Browse this week's nights by date, area, or venue.</span></div>
        <div><strong>2 · Reserve your seat</strong><span>Book on Eventbrite in under a minute. Free and £5 tickets are clearly marked.</span></div>
        <div><strong>3 · Turn up and laugh</strong><span>Your show page has the address, start time, and a map.</span></div></div>
    </section>
    {render_comedians()}
    {render_faq()}
    <section class="section newsletter"><div class="wrap narrow center">
        <p class="eyebrow">Hear about new nights first</p><h2>Never miss a free comedy night</h2>
        <p>Join the mailing list for new venues, one-off special shows, and ticket releases — straight to your inbox.</p>
        <a class="button button-primary" href="/stay-in-touch/">Join the mailing list</a>
    </div></section>"""
    return layout(
        title="Free Comedy Shows in London | Dates, Venues & Tickets",
        description="Free and low-cost stand-up comedy across London. Browse this week's shows, venues and times, and book your free tickets on Eventbrite.",
        canonical="/",
        body=body,
        now=now,
        json_ld=[organizer_schema(), faq_schema()],
        image=f"{BASE_URL}/comedy-audience.jpg",
    )


def render_shows_index(active: dict[str, list[dict]], slugs: dict[str, str], now: dt.datetime) -> str:
    cards = "".join(event_card(preferred_event(events), slugs[key], "shows-directory") for key, events in sorted(active.items(), key=lambda item: event_start(preferred_event(item[1])))) or """
        <div class="empty-state"><h2>New comedy dates are on the way</h2>
            <p>There are no live Eventbrite listings right now. Join the mailing list for new London shows.</p>
            <a class="button button-primary" href="/stay-in-touch/">Join the mailing list</a></div>"""
    body = f"""
    <section class="page-hero"><div class="wrap narrow"><p class="eyebrow">Comedy nights across London</p>
        <h1>Find a London comedy show</h1>
        <p>Every London Comedy Group night currently booking, by day and area. Most shows are free — pick one and reserve your seat on Eventbrite.</p></div></section>
    <section class="section wrap"><h2 class="sr-only">Shows currently booking</h2><div class="event-grid">{cards}</div></section>"""
    return layout(
        title="Comedy Shows in London: Dates, Venues & Tickets | London Comedy Group",
        description="Browse upcoming London Comedy Group stand-up nights. Find free and affordable comedy shows, venue details, dates, and Eventbrite tickets.",
        canonical="/shows/",
        body=body,
        now=now,
        json_ld=[breadcrumb([("Home", "/"), ("Shows", "/shows/")])],
    )


def render_show_page(
    key: str,
    entry: dict,
    events: list[dict],
    active: dict[str, list[dict]],
    slugs: dict[str, str],
    now: dt.datetime,
) -> str:
    slug = entry["slug"]
    is_active = bool(events)
    if is_active:
        event = preferred_event(events)
        title = public_title(event)
        summary = event_summary(event)
        image = best_image(event)
        upcoming = "".join(
            f"""<li><div><strong>{esc(format_full_date(event_start(item)))}</strong><span>{esc(format_time(event_start(item)))} · {esc(ticket_data(item)[0])}</span></div>
                <div class="list-actions"><a class="text-link" href="{esc(event_path(item, slug))}">Details</a>{ticket_button(item, slug, "show-upcoming-list")}</div></li>"""
            for item in events
        )
        next_block = f"""<div class="booking-panel"><p class="eyebrow">Next show</p>
            <h2>{esc(format_full_date(event_start(event)))}</h2>
            <p><strong>{esc(format_time(event_start(event)))}</strong> · {esc(ticket_data(event)[0])}</p>
            <p>{esc(venue_name(event))}<br>{esc(address_display(event))}</p>
            {ticket_button(event, slug, "show-hero")}</div>"""
        venue_block = f"""<h2>Venue details</h2><p><strong>{esc(venue_name(event))}</strong><br>{esc(address_display(event))}</p>
            <a class="text-link" href="https://www.google.com/maps/search/?api=1&amp;query={esc(parse.quote_plus(venue_name(event) + ' ' + address_display(event)))}" rel="noopener noreferrer" target="_blank">Open in Google Maps</a>"""
    else:
        title = entry["title"]
        summary = entry.get("summary") or f"Stand-up comedy at {entry.get('venue_name', 'a London venue')}."
        image = entry.get("image")
        upcoming = "<li><div><strong>No upcoming dates currently listed</strong><span>Browse the live shows directory for another comedy night.</span></div></li>"
        next_block = """<div class="booking-panel"><p class="eyebrow">Currently paused</p>
            <h2>No new dates are listed</h2><p>This night is not currently booking. See the live show directory for the latest options.</p>
            <a class="button button-primary" href="/shows/">Find another comedy show</a></div>"""
        old_address = entry.get("address") or {}
        previous_address = old_address.get("localized_address_display") or ", ".join(
            value
            for value in (old_address.get("address_1"), old_address.get("city"), old_address.get("postal_code"))
            if value
        )
        venue_block = f"<h2>Previous venue</h2><p><strong>{esc(entry.get('venue_name'))}</strong><br>{esc(previous_address)}</p>"
    alternatives = "".join(
        f'<a class="text-link" href="/shows/{esc(slugs[other_key])}/">{esc(public_title(other_events[0]))}</a>'
        for other_key, other_events in active.items()
        if other_key != key
    )
    body = f"""
    <section class="page-hero"><div class="wrap detail-grid"><div><p class="eyebrow">London comedy show</p>
        <h1>{esc(title)}</h1><p>{esc(summary)}</p></div>{next_block}</div></section>
    <section class="section wrap detail-grid"><div><h2>Upcoming dates</h2><ul class="date-list">{upcoming}</ul></div>
        <aside class="info-card">{venue_block}</aside></section>
    <section class="section section-alt"><div class="wrap narrow"><h2>Explore other London comedy nights</h2>
        <div class="link-cloud">{alternatives or '<a class="text-link" href="/shows/">Browse live shows</a>'}</div></div></section>"""
    return layout(
        title=f"{title} | Dates & Tickets | London Comedy Group",
        description=summary,
        canonical=f"/shows/{slug}/",
        body=body,
        now=now,
        image=image,
        json_ld=[breadcrumb([("Home", "/"), ("Shows", "/shows/"), (title, f"/shows/{slug}/")])],
        robots=None if is_active else "noindex,follow",
    )


def schema_address(event: dict) -> dict:
    data = address(event)
    return {
        "@type": "PostalAddress",
        "streetAddress": data.get("address_1") or address_display(event),
        "addressLocality": data.get("city") or "London",
        "postalCode": data.get("postal_code") or "",
        "addressCountry": data.get("country") or "GB",
    }


def render_event_page(event: dict, slug: str, status_name: str, now: dt.datetime) -> str:
    start = event_start(event)
    title = public_title(event)
    source_title = event_title(event)
    summary = event_summary(event)
    path = event_path(event, slug)
    price_label, schema_price, sold_out = ticket_data(event)
    cancelled = status_name == "cancelled"
    expired = status_name == "expired"
    if cancelled:
        status = "Cancelled"
        action = '<a class="button button-primary" href="/shows/">Find another comedy show</a>'
        notice = '<div class="notice"><strong>This event has been cancelled.</strong> Browse the live shows directory for another date.</div>'
    elif expired:
        status = "Event finished" if event_end(event) < now else "No longer listed"
        action = '<a class="button button-primary" href="/shows/">Find an upcoming comedy show</a>'
        notice = '<div class="notice"><strong>This date is no longer booking.</strong> Browse the live shows directory for the latest options.</div>'
    else:
        status = "Sold out" if sold_out else "Booking now"
        action = ticket_button(event, slug, "event-page")
        notice = ""
    offer_availability = "https://schema.org/SoldOut" if sold_out else "https://schema.org/InStock"
    schema = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": source_title,
        "description": summary,
        "image": [best_image(event)],
        "url": absolute(path),
        "startDate": iso_with_zone(start),
        "endDate": iso_with_zone(event_end(event)),
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "eventStatus": "https://schema.org/EventCancelled" if cancelled else "https://schema.org/EventScheduled",
        "location": {"@type": "Place", "name": venue_name(event), "address": schema_address(event)},
        "organizer": {"@type": "Organization", "name": ORG_NAME, "url": BASE_URL},
    }
    if status_name == "live":
        schema["offers"] = {
            "@type": "Offer",
            "url": tracked_url(event["url"], slug, "structured-data"),
            "price": schema_price or "0",
            "priceCurrency": "GBP",
            "availability": offer_availability,
            "validFrom": (event.get("created") or event["start"]["local"]),
        }
    body = f"""
    <section class="page-hero"><div class="wrap detail-grid"><div><p class="eyebrow">London stand-up comedy event</p>
        <h1>{esc(title)}</h1><p>{esc(summary)}</p>{notice}</div>
        <div class="booking-panel"><p class="eyebrow">{esc(status)}</p><h2>{esc(format_full_date(start))}</h2>
            <p><strong>{esc(format_time(start))}</strong> · {esc(price_label)}</p>
            <p>{esc(venue_name(event))}<br>{esc(address_display(event))}</p>{action}</div></div></section>
    <section class="section wrap detail-grid"><div><h2>Plan your night</h2>
        <p><strong>Show time:</strong> {esc(format_time(start))}<br><strong>Ticket price:</strong> {esc(price_label)}</p>
        <p><a class="text-link" href="/shows/{esc(slug)}/">See every upcoming date for this night</a></p></div>
        <aside class="info-card"><h2>Venue</h2><p><strong>{esc(venue_name(event))}</strong><br>{esc(address_display(event))}</p>
        <a class="text-link" href="https://www.google.com/maps/search/?api=1&amp;query={esc(parse.quote_plus(venue_name(event) + ' ' + address_display(event)))}" rel="noopener noreferrer" target="_blank">Open in Google Maps</a></aside></section>"""
    return layout(
        title=f"{title} - {format_day(start)} | Tickets",
        description=f"{title} at {venue_name(event)}, {public_area(event)}, on {format_full_date(start)} at {format_time(start)}. {price_label}. Reserve on Eventbrite.",
        canonical=path,
        body=body,
        now=now,
        image=best_image(event),
        json_ld=[
            schema,
            breadcrumb([("Home", "/"), ("Shows", "/shows/"), (title, f"/shows/{slug}/"), (format_day(start), path)]),
        ],
        robots="noindex,follow" if expired else None,
    )


def render_static_pages(now: dt.datetime) -> dict[str, str]:
    hire_body = f"""
    <section class="page-hero"><div class="wrap narrow"><p class="eyebrow">Comedy for your event or venue</p>
        <h1>Hire comedians in London</h1><p>Planning a private party, company event, charity night, or a regular venue show? Tell us what you need and London Comedy Group will help shape the right comedy night.</p>
        <a class="button button-primary" href="{esc(HIRE_FORM)}" rel="noopener noreferrer" target="_blank">Tell us about your event</a></div></section>
    <div class="section wrap feature-list three"><div><strong>Venue comedy nights</strong><span>Bring recurring stand-up to your room.</span></div>
        <div><strong>Private and corporate events</strong><span>Comedy tailored to your audience and occasion.</span></div>
        <div><strong>Charity nights and workshops</strong><span>Tell us what you are planning, including the format, venue, and budget.</span></div></div>"""
    perform_body = f"""
    <section class="page-hero"><div class="wrap narrow"><p class="eyebrow">For comedians</p>
        <h1>Perform with London Comedy Group</h1><p>Apply for spots at London Comedy Group nights using the performer form. Share your details once and the team can contact you when a suitable opportunity comes up.</p>
        <a class="button button-primary" href="{esc(PERFORM_FORM)}" rel="noopener noreferrer" target="_blank">Apply to perform</a></div></section>"""
    newsletter_body = f"""
    <section class="page-hero"><div class="wrap narrow center"><p class="eyebrow">Stay in touch</p>
        <h1>Get London comedy nights in your inbox</h1><p>Join the London Comedy Group mailing list for new venues, special shows, and ticket releases.</p>
        <a class="button button-primary" href="{esc(BEEHIIV)}" rel="noopener noreferrer" target="_blank">Join the mailing list</a></div></section>"""
    redirect_body = """<section class="page-hero"><div class="wrap narrow center"><h1>Mailing list moved</h1>
        <p>The London Comedy Group mailing list now has a simpler home.</p><a class="button button-primary" href="/stay-in-touch/">Go to the mailing list</a></div></section>"""
    not_found_body = """<section class="page-hero"><div class="wrap narrow center"><p class="eyebrow">404</p><h1>That page is not on the bill</h1>
        <p>Find a live London comedy show instead.</p><a class="button button-primary" href="/shows/">See upcoming shows</a></div></section>"""
    redirect = layout(title="Mailing List | London Comedy Group", description="Join the London Comedy Group mailing list.", canonical="/stay-in-touch/", body=redirect_body, now=now, robots="noindex,follow")
    redirect = redirect.replace("</head>", '    <meta http-equiv="refresh" content="0; url=/stay-in-touch/">\n</head>')
    return {
        "hire-comedians-london/index.html": layout(title="Hire Comedians in London | London Comedy Group", description="Hire London comedians for venue nights, corporate events, private parties, charity events, and comedy workshops.", canonical="/hire-comedians-london/", body=hire_body, now=now, json_ld=[breadcrumb([("Home", "/"), ("Hire comedians", "/hire-comedians-london/")])]),
        "perform-with-us/index.html": layout(title="Perform With Us | London Comedy Group", description="Apply to perform at London Comedy Group stand-up nights across London.", canonical="/perform-with-us/", body=perform_body, now=now, robots="noindex,follow"),
        "stay-in-touch/index.html": layout(title="London Comedy Shows Mailing List | London Comedy Group", description="Join the London Comedy Group mailing list for new venues, special comedy shows, and ticket releases.", canonical="/stay-in-touch/", body=newsletter_body, now=now),
        "let-us-talk-to-you.html": redirect,
        "404.html": layout(title="Page Not Found | London Comedy Group", description="Find upcoming London Comedy Group stand-up shows.", canonical="/404.html", body=not_found_body, now=now, robots="noindex,follow"),
    }


def route_file(path: str) -> str:
    return "index.html" if path == "/" else path.lstrip("/") + ("index.html" if path.endswith("/") else "")


def significant_lastmods(paths: list[str], generated: dict[str, str], state: dict, today: str) -> dict[str, str]:
    previous = state.setdefault("lastmod", {})
    current: dict[str, str] = {}
    for path in paths:
        relative = route_file(path)
        old_text = (ROOT / relative).read_text(encoding="utf-8") if (ROOT / relative).exists() else None
        current[path] = today if old_text != generated[relative] else previous.get(path, today)
    state["lastmod"] = current
    return current


def sitemap(paths: list[str], lastmods: dict[str, str]) -> str:
    urls = "\n".join(f"  <url><loc>{esc(absolute(path))}</loc><lastmod>{lastmods[path]}</lastmod></url>" for path in paths)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls}
</urlset>
"""


def cleanup(previous: list[str], current: set[str]) -> None:
    for relative in previous:
        if relative in current:
            continue
        path = ROOT / relative
        if path.exists() and path.is_file():
            path.unlink()
        parent = path.parent
        while parent != ROOT:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def refresh_heartbeat(now: dt.datetime) -> None:
    previous = None
    if HEARTBEAT.exists():
        try:
            previous = dt.date.fromisoformat(HEARTBEAT.read_text(encoding="utf-8").strip())
        except ValueError:
            pass
    if previous is None or previous + dt.timedelta(days=HEARTBEAT_INTERVAL_DAYS) <= now.date():
        write_text(HEARTBEAT, now.date().isoformat() + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", help="Read Eventbrite-shaped JSON instead of calling the API.")
    parser.add_argument("--now", help="Override the current ISO datetime for deterministic generation.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    now = parse_local(args.now) if args.now else dt.datetime.now(LONDON)
    today = now.date().isoformat()
    live, cancelled = load_events(args.fixture)
    live = dedupe_occurrences([event for event in live if event_end(event) >= now])
    cancelled = dedupe_occurrences([event for event in cancelled if retention_deadline(event) >= now])
    state = load_json(STATE, {"series": {}})
    overrides = load_json(OVERRIDES, {})
    for event in [*live, *cancelled]:
        override = {**overrides.get(series_key(event), {}), **overrides.get(str(event["id"]), {})}
        if override.get("title"):
            event["name"] = {"text": override["title"]}
        for field in ("summary", "url"):
            if override.get(field):
                event[field] = override[field]
    slugs = update_state(live, state, today)
    occurrences = update_occurrences(live, cancelled, state, slugs, now)
    active = group_by_series(live)
    generated: dict[str, str] = {
        "index.html": render_home(active, slugs, now),
        "shows/index.html": render_shows_index(active, slugs, now),
        "robots.txt": f"User-agent: *\nAllow: /\n\nSitemap: {BASE_URL}/sitemap.xml\n",
    }
    generated.update(render_static_pages(now))

    for key, entry in state["series"].items():
        generated[f"shows/{entry['slug']}/index.html"] = render_show_page(
            key, entry, active.get(key, []), active, slugs, now
        )

    indexable_event_paths: list[str] = []
    for item in sorted(occurrences.values(), key=lambda value: value["path"]):
        event = {**item["event"], "_site_path": item["path"]}
        key = series_key(event)
        entry = state["series"].get(key)
        if not entry:
            continue
        slug = entry["slug"]
        path = event_path(event, slug)
        generated[path.lstrip("/") + "index.html"] = render_event_page(event, slug, item["status"], now)
        if item["status"] in {"live", "cancelled"}:
            indexable_event_paths.append(path)

    sitemap_paths = [
        "/",
        "/shows/",
        "/hire-comedians-london/",
        "/stay-in-touch/",
        *[f"/shows/{slugs[key]}/" for key in active],
        *indexable_event_paths,
    ]
    generated["sitemap.xml"] = sitemap(sitemap_paths, significant_lastmods(sitemap_paths, generated, state, today))

    previous = load_json(MANIFEST, [])
    current = set(generated)
    cleanup(previous, current)
    for relative, text in generated.items():
        write_text(ROOT / relative, text)
    save_json(STATE, state)
    save_json(MANIFEST, sorted(current))
    refresh_heartbeat(now)
    print(f"Generated {len(generated)} files from {len(live)} live events across {len(active)} shows.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (error.HTTPError, error.URLError, RuntimeError) as ex:
        print(f"Eventbrite refresh failed: {ex}", file=sys.stderr)
        raise SystemExit(2)

#!/usr/bin/env python3
"""
Auto-update the "Upcoming Shows" grid in index.html from Eventbrite.

Pulls every LIVE event for the London Comedy Group organization, collapses each
recurring series down to its next occurrence, and rewrites the cards between the
<!-- EVENTS:START --> / <!-- EVENTS:END --> markers in index.html.

Needs one secret: the Eventbrite PRIVATE token, supplied via the EVENTBRITE_TOKEN
environment variable (set as a GitHub Actions secret). Stdlib only - no pip install.

Run locally:  EVENTBRITE_TOKEN=xxxx python3 scripts/update-events.py
"""

import os
import sys
import json
import html
import datetime
import re
import unicodedata
from urllib import request, parse, error

ORG_ID = "1234539271983"                       # London Comedy Group
API = "https://www.eventbriteapi.com/v3"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index.html")
OVERRIDES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "event-overrides.json")
START_MARK = "<!-- EVENTS:START (auto-generated from Eventbrite - do not edit by hand) -->"
END_MARK = "<!-- EVENTS:END -->"


def api_get(path, params):
    qs = parse.urlencode(params)
    req = request.Request(f"{API}{path}?{qs}",
                          headers={"Authorization": f"Bearer {TOKEN}"})
    with request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_live_events():
    """All live events for the org, following pagination."""
    events, page = [], 1
    while True:
        data = api_get(f"/organizations/{ORG_ID}/events/", {
            "status": "live",
            "order_by": "start_asc",
            "expand": "venue,ticket_availability",
            "page_size": 50,
            "page": page,
        })
        events.extend(data.get("events", []))
        pg = data.get("pagination", {})
        if not pg.get("has_more_items"):
            break
        page += 1
    return events


def dedupe_series(events):
    """Keep the earliest upcoming occurrence per series (or per event if no series)."""
    seen = {}
    for e in events:
        key = e.get("series_id") or e["id"]
        current = seen.get(key)
        if current is None or e["start"]["utc"] < current["start"]["utc"]:
            seen[key] = e
    return list(seen.values())


def normalized_text(value):
    """Normalize Eventbrite text so formatting differences do not defeat deduping."""
    text = unicodedata.normalize("NFKD", value or "").casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def occurrence_key(e):
    """Identify duplicate listings for the same performance."""
    venue = e.get("venue") or {}
    address = venue.get("address") or {}
    place = address.get("localized_address_display") or venue.get("name") or ""
    return normalized_text(place), e["start"]["local"]


def dedupe_occurrences(events):
    """Collapse separately-created Eventbrite listings for the same show occurrence."""
    seen = {}
    for e in events:
        key = occurrence_key(e)
        current = seen.get(key)
        if current is None or e["id"] < current["id"]:
            seen[key] = e
    return list(seen.values())


def fmt_time(local_iso):
    # "2026-06-05T18:00:00" -> "6:00 PM"
    dt = datetime.datetime.fromisoformat(local_iso)
    return dt.strftime("%I:%M %p").lstrip("0")


def next_show_badge(local_iso):
    dt = datetime.datetime.fromisoformat(local_iso)
    return f"Next show: {dt.strftime('%A')} {dt.day} {dt.strftime('%B')}"


def fmt_price(ta):
    minp = (ta or {}).get("minimum_ticket_price") or {}
    maxp = (ta or {}).get("maximum_ticket_price") or {}
    lo = minp.get("major_value")
    hi = maxp.get("major_value")
    if lo is None:
        return None, False
    is_free = float(lo) == 0 and (hi is None or float(hi) == 0)
    if is_free:
        return "FREE ENTRY", True

    def money(v):
        f = float(v)
        return f"£{int(f)}" if f == int(f) else f"£{f:.2f}"

    if hi and float(hi) != float(lo):
        return f"{money(lo)} - {money(hi)}", False
    return money(lo), False


def venue_line(e):
    v = e.get("venue") or {}
    name = v.get("name")
    addr = (v.get("address") or {}).get("localized_address_display")
    parts = [p for p in (name, addr) if p]
    return ", ".join(parts) if parts else "London"


def best_image(e):
    logo = e.get("logo") or {}
    orig = (logo.get("original") or {}).get("url")
    return orig or logo.get("url")


def build_card(e, overrides):
    o = overrides.get(str(e.get("series_id") or e["id"]), {})

    title = o.get("title", e["name"]["text"])
    image = o.get("image", best_image(e))
    badge = o.get("date_badge", next_show_badge(e["start"]["local"]))
    time = o.get("time", fmt_time(e["start"]["local"]))
    venue = o.get("venue", venue_line(e))
    blurb = o.get("blurb", (e.get("summary") or "").strip())

    auto_price, is_free = fmt_price(e.get("ticket_availability"))
    price = o.get("price", auto_price)
    cta = o.get("cta", "Get Free Tickets" if is_free else "Get Tickets")
    url = o.get("url", e["url"])

    esc = html.escape
    rows = [
        f'                <img src="{esc(image or "")}" alt="{esc(title)}" '
        f'style="width: 100%; height: 200px; object-fit: cover; border-radius: 10px; margin-bottom: 20px;">',
        f'                <span class="event-date">{esc(badge)}</span>',
        f'                <h3 class="event-title">{esc(title)}</h3>',
        f'                <p class="event-location">📍 {esc(venue)}</p>',
        f'                <p class="event-time">🕐 {esc(time)}</p>',
    ]
    if price:
        rows.append(f'                <p class="event-price">{esc(price)}</p>')
    if blurb:
        rows.append(f'                <p style="color: rgba(255,255,255,0.7); margin-top: 15px;">{esc(blurb)}</p>')
    rows.append(
        f'                <a href="{esc(url)}" target="_blank" class="btn btn-secondary" '
        f'style="margin-top: 15px; padding: 10px 20px; font-size: 14px;">\n'
        f'                    {esc(cta)} →\n'
        f'                </a>'
    )
    inner = "\n".join(rows)
    return f'            <div class="event-card fade-in">\n{inner}\n            </div>'


def main():
    overrides = {}
    if os.path.exists(OVERRIDES):
        with open(OVERRIDES, encoding="utf-8") as f:
            overrides = json.load(f)

    events = dedupe_occurrences(dedupe_series(fetch_live_events()))
    events.sort(key=lambda e: e["start"]["local"])
    if not events:
        print("No live events returned - leaving index.html untouched.", file=sys.stderr)
        return 1

    cards = "\n\n".join(build_card(e, overrides) for e in events)
    block = f"{START_MARK}\n{cards}\n            {END_MARK}"

    with open(INDEX, encoding="utf-8") as f:
        page = f.read()

    if START_MARK not in page or END_MARK not in page:
        print("Markers not found in index.html - aborting.", file=sys.stderr)
        return 2

    pre, rest = page.split(START_MARK, 1)
    _, post = rest.split(END_MARK, 1)
    new_page = f"{pre}{block}{post}"

    if new_page == page:
        print(f"No changes - {len(events)} events already up to date.")
        return 0

    with open(INDEX, "w", encoding="utf-8") as f:
        f.write(new_page)
    print(f"Updated index.html with {len(events)} events.")
    return 0


if __name__ == "__main__":
    TOKEN = os.environ.get("EVENTBRITE_TOKEN")
    if not TOKEN:
        print("ERROR: set EVENTBRITE_TOKEN environment variable.", file=sys.stderr)
        sys.exit(3)
    try:
        sys.exit(main())
    except error.HTTPError as ex:
        print(f"Eventbrite API error {ex.code}: {ex.read().decode(errors='ignore')[:300]}", file=sys.stderr)
        sys.exit(4)

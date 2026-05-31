#!/usr/bin/env python3
"""Fast, dependency-free validation for generated website files."""

from __future__ import annotations

import datetime as dt
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "scripts" / "generated-files.json"
BASE_URL = "https://londoncomedygroup.com"


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.canonical = []
        self.descriptions = []
        self.robots = []
        self.h1 = []
        self.images = []
        self.links = []
        self.json_ld = []
        self._json_script = False
        self._json_parts = []

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == "link" and data.get("rel") == "canonical":
            self.canonical.append(data.get("href"))
        if tag == "meta" and data.get("name") == "description":
            self.descriptions.append(data.get("content"))
        if tag == "meta" and data.get("name") == "robots":
            self.robots.append(data.get("content"))
        if tag == "h1":
            self.h1.append(tag)
        if tag == "img":
            self.images.append(data)
        if tag == "a" and data.get("href"):
            self.links.append(data["href"])
        if tag == "script" and data.get("type") == "application/ld+json":
            self._json_script = True
            self._json_parts = []

    def handle_data(self, data):
        if self._json_script:
            self._json_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._json_script:
            self._json_script = False
            self.json_ld.append("".join(self._json_parts))


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def local_target(path: Path, href: str) -> Path | None:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc or href.startswith("#"):
        return None
    route = parsed.path
    if not route:
        return None
    if route.startswith("/"):
        target = ROOT / route.lstrip("/")
    else:
        target = path.parent / route
    if route.endswith("/"):
        target = target / "index.html"
    return target


def public_url(relative: Path) -> str:
    value = relative.as_posix()
    if value == "index.html":
        return BASE_URL + "/"
    if value.endswith("/index.html"):
        return BASE_URL + "/" + value.removesuffix("index.html")
    return BASE_URL + "/" + value


def main() -> int:
    errors: list[str] = []
    generated = json.loads(MANIFEST.read_text(encoding="utf-8"))
    html_files = [ROOT / item for item in generated if item.endswith(".html")]
    sitemap_root = ElementTree.parse(ROOT / "sitemap.xml").getroot()
    sitemap_urls = {
        item.text
        for item in sitemap_root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url/{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
    }
    if not html_files:
        fail(errors, "No generated HTML files found.")

    for path in html_files:
        if not path.exists():
            fail(errors, f"Missing generated file: {path.relative_to(ROOT)}")
            continue
        parser = PageParser()
        parser.feed(path.read_text(encoding="utf-8"))
        relative = path.relative_to(ROOT)
        if len(parser.canonical) != 1:
            fail(errors, f"{relative}: expected one canonical link.")
        if len(parser.descriptions) != 1:
            fail(errors, f"{relative}: expected one meta description.")
        if len(parser.h1) != 1:
            fail(errors, f"{relative}: expected one h1.")
        if any("noindex" in (tag or "") for tag in parser.robots) and public_url(relative) in sitemap_urls:
            fail(errors, f"{relative}: noindex page must not appear in sitemap.xml.")
        for image in parser.images:
            if "alt" not in image:
                fail(errors, f"{relative}: image missing alt attribute.")
            if image.get("src", "").startswith("/assets/"):
                continue
            if "width" not in image or "height" not in image:
                fail(errors, f"{relative}: image missing width and height.")
        for href in parser.links:
            target = local_target(path, href)
            if target and not target.exists():
                fail(errors, f"{relative}: broken local link {href}")
        for payload in parser.json_ld:
            try:
                json.loads(payload)
            except json.JSONDecodeError as ex:
                fail(errors, f"{relative}: invalid JSON-LD ({ex}).")

    for required in ("robots.txt", "sitemap.xml", "assets/site.css", "assets/site.js"):
        if not (ROOT / required).exists():
            fail(errors, f"Missing required file: {required}")

    if len(sitemap_urls) != len(sitemap_root):
        fail(errors, "sitemap.xml: duplicate or missing URL entries.")
    for item in sitemap_root:
        lastmod = item.find("{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod")
        try:
            if lastmod is None:
                raise ValueError
            dt.date.fromisoformat(lastmod.text or "")
        except ValueError:
            fail(errors, "sitemap.xml: each URL needs an ISO lastmod date.")

    if errors:
        print("Website validation failed:", file=sys.stderr)
        for item in errors:
            print(f"- {item}", file=sys.stderr)
        return 1
    print(f"Validated {len(html_files)} generated HTML files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

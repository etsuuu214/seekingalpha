#!/usr/bin/env python3
from __future__ import annotations

import email.utils
import hashlib
import html
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

LIST_URL = "https://seekingalpha.com/market-news/earnings-calls-insights"
OUTPUT_FILE = "seekingalpha_earnings_calls_insights.xml"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)
TIMEOUT = 30
MAX_ITEMS = 60


@dataclass
class Item:
    title: str
    link: str
    guid: str
    description: str
    pub_date: datetime


def fetch(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def extract_article_links(list_html: str) -> List[str]:
    soup = BeautifulSoup(list_html, "html.parser")
    links: List[str] = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        full = urljoin("https://seekingalpha.com", href)
        if not full.startswith("https://seekingalpha.com/news/"):
            continue
        if full in seen:
            continue
        title = clean_text(a.get_text(" ", strip=True))
        if len(title) < 20:
            continue
        seen.add(full)
        links.append(full)

    return links[:MAX_ITEMS]


def try_parse_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = dateparser.parse(value)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def extract_pub_date(article_html: str) -> datetime:
    soup = BeautifulSoup(article_html, "html.parser")

    candidates = []
    for tag in soup.find_all(["time", "meta"]):
        for key in ("datetime", "content"):
            value = tag.get(key)
            dt = try_parse_date(value)
            if dt:
                candidates.append(dt)

    scripts_text = "\n".join(script.get_text(" ", strip=True) for script in soup.find_all("script"))
    for pattern in [
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'"dateModified"\s*:\s*"([^"]+)"',
        r'published\s*[:=]\s*"([^"]+)"',
    ]:
        m = re.search(pattern, scripts_text)
        if m:
            dt = try_parse_date(m.group(1))
            if dt:
                candidates.append(dt)

    if candidates:
        return sorted(candidates)[0]

    return datetime.now(timezone.utc)


def extract_title(article_html: str) -> str:
    soup = BeautifulSoup(article_html, "html.parser")
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return clean_text(og["content"])
    if soup.title and soup.title.text:
        return clean_text(soup.title.text.replace("| Seeking Alpha", ""))
    h1 = soup.find("h1")
    if h1:
        return clean_text(h1.get_text(" ", strip=True))
    return "Seeking Alpha Earnings Call Insight"


def extract_description(article_html: str) -> str:
    soup = BeautifulSoup(article_html, "html.parser")
    for attr in [
        {"name": "description"},
        {"property": "og:description"},
        {"name": "twitter:description"},
    ]:
        tag = soup.find("meta", attrs=attr)
        if tag and tag.get("content"):
            return clean_text(tag["content"])
    return "Seeking Alpha Earnings Calls Insights item"


def build_items() -> List[Item]:
    list_html = fetch(LIST_URL)
    article_links = extract_article_links(list_html)
    items: List[Item] = []

    if not article_links:
        raise RuntimeError("No article links found on listing page.")

    for link in article_links:
        try:
            article_html = fetch(link)
            title = extract_title(article_html)
            description = extract_description(article_html)
            pub_date = extract_pub_date(article_html)
            guid = link
            items.append(Item(title=title, link=link, guid=guid, description=description, pub_date=pub_date))
        except Exception as exc:
            print(f"WARN: failed to parse {link}: {exc}", file=sys.stderr)

    items.sort(key=lambda x: x.pub_date, reverse=True)
    return items


def rfc2822(dt: datetime) -> str:
    return email.utils.format_datetime(dt.astimezone(timezone.utc))


def xml_escape(text: str) -> str:
    return html.escape(text or "", quote=True)


def render_rss(items: List[Item]) -> str:
    build_date = datetime.now(timezone.utc)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        '  <channel>',
        '    <title>Seeking Alpha - Earnings Calls Insights (custom)</title>',
        f'    <link>{xml_escape(LIST_URL)}</link>',
        '    <description>Custom RSS feed built from the public Seeking Alpha Earnings Calls Insights listing.</description>',
        '    <language>en-US</language>',
        f'    <lastBuildDate>{xml_escape(rfc2822(build_date))}</lastBuildDate>',
        f'    <ttl>60</ttl>',
    ]

    for item in items:
        lines.extend([
            '    <item>',
            f'      <title>{xml_escape(item.title)}</title>',
            f'      <link>{xml_escape(item.link)}</link>',
            f'      <guid isPermaLink="true">{xml_escape(item.guid)}</guid>',
            f'      <pubDate>{xml_escape(rfc2822(item.pub_date))}</pubDate>',
            f'      <description>{xml_escape(item.description)}</description>',
            '    </item>',
        ])

    lines.extend([
        '  </channel>',
        '</rss>',
        '',
    ])
    return "\n".join(lines)


def main() -> int:
    items = build_items()
    if not items:
        raise RuntimeError("No items parsed; RSS file not written.")
    xml = render_rss(items)
    Path(OUTPUT_FILE).write_text(xml, encoding="utf-8")
    digest = hashlib.sha256(xml.encode("utf-8")).hexdigest()
    print(f"Wrote {OUTPUT_FILE} with {len(items)} items. sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from assistant_framework.ingestion import append_normalized_records, normalize_collected_item, write_raw_snapshot
from assistant_framework.workspace import Workspace

NAME = "news_headlines"
DESCRIPTION = "Collects up to 100 recent headlines from a balanced set of public RSS and Atom news feeds."
INTERVAL_SECONDS = 86400
LOOKBACK_HOURS = 24
TARGET_HEADLINES = 100
OUTPUT_FILE = "news/headlines.last_24h.jsonl"
STATUS_FILE = "collectors/news_headlines/status.json"
FILE_STRUCTURE = ["collectors/news_headlines/__init__.py", "collectors/news_headlines/README.md"]
GENERATED_FILES = [OUTPUT_FILE, STATUS_FILE, f"collected/normalized/{NAME}.jsonl", "collected/raw/news_headlines"]
USER_AGENT = "buducca-news-collector/1.0"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
DEFAULT_SOURCES = [
    {"name": "Associated Press", "url": "https://apnews.com/hub/ap-top-news?output=rss"},
    {"name": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "NPR News", "url": "https://feeds.npr.org/1001/rss.xml"},
    {"name": "The Guardian World", "url": "https://www.theguardian.com/world/rss"},
    {"name": "CBS News", "url": "https://www.cbsnews.com/latest/rss/main"},
    {"name": "ABC News", "url": "https://abcnews.go.com/abcnews/topstories"},
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index"},
]


def register_collector(config: dict[str, Any]):
    interval = float(config.get("interval_seconds", INTERVAL_SECONDS))
    timeout = float(config.get("timeout_seconds", 20))
    user_agent = str(config.get("user_agent") or USER_AGENT)
    target_count = max(1, int(config.get("target_count", TARGET_HEADLINES)))
    lookback = timedelta(hours=max(1, int(config.get("lookback_hours", LOOKBACK_HOURS))))
    sources = _normalize_sources(config.get("sources") or DEFAULT_SOURCES)

    def _run(workspace: Workspace) -> None:
        now = datetime.now(timezone.utc)
        cutoff = now - lookback
        fetched_sources: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        for source in sources:
            try:
                items = _fetch_feed(source["url"], timeout_seconds=timeout, user_agent=user_agent)
                recent_items = [item for item in items if item["published_dt"] >= cutoff]
                fetched_sources.append(
                    {
                        "source_id": source["id"],
                        "source_name": source["name"],
                        "url": source["url"],
                        "item_count": len(recent_items),
                        "items": [_serialize_headline(item) for item in recent_items],
                    }
                )
                candidates.extend(recent_items)
            except Exception as exc:
                errors.append({"source": source["name"], "error": str(exc)})

        selected = _select_balanced_headlines(candidates, target_count=target_count)
        write_raw_snapshot(
            workspace,
            NAME,
            {
                "collected_at": now.isoformat(),
                "lookback_hours": int(lookback.total_seconds() // 3600),
                "target_count": target_count,
                "sources": fetched_sources,
                "errors": errors,
                "selected_count": len(selected),
            },
        )

        output_rows = []
        normalized_records = []
        for item in selected:
            record = {
                "source": "news",
                "collector": NAME,
                "collected_at": now.isoformat(),
                **_serialize_headline(item),
            }
            output_rows.append(record)
            normalized_records.append(
                normalize_collected_item(
                    source="news",
                    timestamp=item["published_at"],
                    title=item["title"],
                    text=item["summary"],
                    metadata={
                        "collector": NAME,
                        "source_id": item["source_id"],
                        "source_name": item["source_name"],
                        "url": item["url"],
                    },
                )
            )

        workspace.write_text(
            OUTPUT_FILE,
            "\n".join(json.dumps(item, ensure_ascii=False) for item in output_rows) + ("\n" if output_rows else ""),
        )
        append_normalized_records(workspace, NAME, normalized_records)
        workspace.write_text(
            STATUS_FILE,
            json.dumps(
                {
                    "last_attempt_at": now.isoformat(),
                    "last_success_at": now.isoformat(),
                    "last_error": None,
                    "target_count": target_count,
                    "selected_count": len(output_rows),
                    "source_count": len(sources),
                    "error_count": len(errors),
                    "errors": errors,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )

        if not output_rows and errors:
            raise RuntimeError("; ".join(f'{entry["source"]}: {entry["error"]}' for entry in errors))

    return {
        "name": NAME,
        "description": DESCRIPTION,
        "interval_seconds": interval,
        "generated_files": GENERATED_FILES,
        "file_structure": FILE_STRUCTURE,
        "run": _run,
    }


def _normalize_sources(raw_value: Any) -> list[dict[str, str]]:
    if not isinstance(raw_value, list) or not raw_value:
        raise ValueError("sources must be a non-empty list")

    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_value:
        if not isinstance(item, dict):
            raise ValueError("each source must be an object")
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        source_id = str(item.get("id") or _slugify(name)).strip()
        if not name or not url:
            raise ValueError("each source must include name and url")
        if source_id in seen:
            raise ValueError(f"duplicate source id: {source_id}")
        seen.add(source_id)
        sources.append({"id": source_id, "name": name, "url": url})
    return sources


def _slugify(value: str) -> str:
    chars = [ch.lower() if ch.isalnum() else "_" for ch in value.strip()]
    slug = "".join(chars).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "source"


def _fetch_feed(url: str, *, timeout_seconds: float, user_agent: str) -> list[dict[str, Any]]:
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/rss+xml, application/atom+xml, application/xml"})
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = response.read()
    return _parse_feed(payload, feed_url=url)


def _parse_feed(payload: bytes, *, feed_url: str) -> list[dict[str, Any]]:
    root = ElementTree.fromstring(payload)
    headlines: list[dict[str, Any]] = []

    channel = root.find("channel")
    if channel is not None:
        source_name = _clean_text(channel.findtext("title")) or feed_url
        for item in channel.findall("item"):
            headline = _parse_rss_item(item, source_name=source_name, feed_url=feed_url)
            if headline is not None:
                headlines.append(headline)
        return headlines

    if root.tag == f"{ATOM_NS}feed":
        source_name = _clean_text(root.findtext(f"{ATOM_NS}title")) or feed_url
        for entry in root.findall(f"{ATOM_NS}entry"):
            headline = _parse_atom_entry(entry, source_name=source_name, feed_url=feed_url)
            if headline is not None:
                headlines.append(headline)
        return headlines

    raise ValueError(f"unsupported feed format: {feed_url}")


def _parse_rss_item(item: ElementTree.Element, *, source_name: str, feed_url: str) -> dict[str, Any] | None:
    title = _clean_text(item.findtext("title"))
    link = _clean_text(item.findtext("link"))
    summary = _clean_text(item.findtext("description"))
    published = (
        _clean_text(item.findtext("pubDate"))
        or _clean_text(item.findtext("date"))
        or _clean_text(item.findtext("{http://purl.org/dc/elements/1.1/}date"))
    )
    if not title or not link or not published:
        return None
    published_dt = _parse_datetime(published)
    return {
        "source_id": _slugify(source_name),
        "source_name": source_name,
        "feed_url": feed_url,
        "title": title,
        "url": link,
        "summary": summary,
        "published_at": published_dt.isoformat(),
        "published_dt": published_dt,
    }


def _parse_atom_entry(entry: ElementTree.Element, *, source_name: str, feed_url: str) -> dict[str, Any] | None:
    title = _clean_text(entry.findtext(f"{ATOM_NS}title"))
    summary = _clean_text(entry.findtext(f"{ATOM_NS}summary")) or _clean_text(entry.findtext(f"{ATOM_NS}content"))
    published = _clean_text(entry.findtext(f"{ATOM_NS}published")) or _clean_text(entry.findtext(f"{ATOM_NS}updated"))
    link = ""
    for link_node in entry.findall(f"{ATOM_NS}link"):
        rel = (link_node.attrib.get("rel") or "alternate").strip()
        href = (link_node.attrib.get("href") or "").strip()
        if rel == "alternate" and href:
            link = href
            break
        if not link and href:
            link = href
    if not title or not link or not published:
        return None
    published_dt = _parse_datetime(published)
    return {
        "source_id": _slugify(source_name),
        "source_name": source_name,
        "feed_url": feed_url,
        "title": title,
        "url": link,
        "summary": summary,
        "published_at": published_dt.isoformat(),
        "published_dt": published_dt,
    }


def _parse_datetime(raw_value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(raw_value)
    except Exception:
        parsed = None
    if parsed is None:
        normalized = raw_value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _select_balanced_headlines(items: list[dict[str, Any]], *, target_count: int) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    seen_keys: set[str] = set()

    for item in sorted(items, key=lambda entry: entry["published_dt"], reverse=True):
        dedupe_key = f'{item["url"]}\n{item["title"].casefold()}'
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        buckets.setdefault(item["source_id"], []).append(item)

    ordered_source_ids = sorted(
        buckets,
        key=lambda source_id: (
            buckets[source_id][0]["published_dt"],
            source_id,
        ),
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    while len(selected) < target_count:
        added = False
        for source_id in ordered_source_ids:
            source_items = buckets[source_id]
            if not source_items:
                continue
            selected.append(source_items.pop(0))
            added = True
            if len(selected) >= target_count:
                break
        if not added:
            break

    return selected


def _serialize_headline(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": item["source_id"],
        "source_name": item["source_name"],
        "feed_url": item["feed_url"],
        "title": item["title"],
        "url": item["url"],
        "summary": item["summary"],
        "published_at": item["published_at"],
    }

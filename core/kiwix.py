# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Offline-only Kiwix ZIM encyclopedia search and extraction client.

Provides a lightweight cached internet connectivity check (`is_online`),
query filtering to skip greetings and mesh telemetry commands, and XML search +
HTML article excerpt extraction against a local `kiwix-serve` instance.
"""

from html.parser import HTMLParser
import logging
import re
import socket
import threading
import time
from typing import Optional
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger(__name__)

DEFAULT_PROBE_HOSTS = (("1.1.1.1", 443), ("8.8.8.8", 53))

_ONLINE_LOCK = threading.Lock()
_ONLINE_CACHE = {"ts": 0.0, "online": None}

_GREETINGS_AND_ACKS = frozenset({
    "hi", "hello", "hey", "yo", "thanks", "thank you", "thx", "ok", "okay",
    "yes", "no", "yep", "nope", "lol", "test", "ping", "copy", "roger", "bye",
    "good morning", "good afternoon", "good evening", "good night", "gm", "gn",
})

_MESH_TELEMETRY_RE = re.compile(
    r"\b(snr|rssi|hops?|repeater|meshcore|meshtastic|node\s+id|"
    r"who\s+is\s+online|who's\s+online|remind\s+me|alert\s+me|"
    r"cancel\s+(all\s+)?(my\s+)?(alert|reminder|task)s?|"
    r"list\s+(my\s+)?(alert|reminder|task)s?|"
    r"my\s+battery|bot\s+battery|node\s+battery|"
    r"my\s+location|my\s+route|my\s+path|what\s+route|what\s+path|"
    r"how\s+many\s+hops)\b",
    re.IGNORECASE,
)

_GLUE_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "am",
    "do", "does", "did", "can", "could", "would", "should", "will", "shall",
    "may", "might", "must", "i", "me", "my", "we", "us", "our", "you", "your",
    "they", "them", "their", "he", "she", "it", "this", "that", "these",
    "those", "what", "which", "who", "whom", "whose", "where", "when", "why",
    "how", "to", "of", "in", "on", "at", "by", "for", "with", "about", "from",
    "into", "onto", "out", "up", "down", "off", "over", "under", "and", "or",
    "but", "if", "then", "than", "so", "as", "not", "no", "yes", "there",
    "here", "please", "tell",
})

_FILLER_STOP_WORDS = _GLUE_STOP_WORDS | frozenset({
    "help", "explain", "describe", "give", "show", "find", "search", "lookup",
    "look", "treat", "treatment", "fix", "make", "use", "using", "get", "need",
    "want", "know", "best", "way", "ways", "step", "steps", "guide", "someone",
    "somebody", "person", "people", "something", "anything", "much", "many",
    "long", "often", "properly", "safely", "correctly", "apply", "perform",
    "start", "stop", "check", "symptoms", "causes", "signs",
})


def reset_online_cache() -> None:
    """Clear the cached internet connectivity probe state."""
    with _ONLINE_LOCK:
        _ONLINE_CACHE["ts"] = 0.0
        _ONLINE_CACHE["online"] = None


def is_online(
    probe_hosts=DEFAULT_PROBE_HOSTS,
    timeout: float = 1.2,
    cache_ttl: float = 60.0,
) -> bool:
    """Return True if an upstream internet host responds to a quick TCP probe."""
    now = time.monotonic()
    with _ONLINE_LOCK:
        cached = _ONLINE_CACHE["online"]
        if cached is not None and cache_ttl > 0 and (now - _ONLINE_CACHE["ts"]) < cache_ttl:
            return cached

    reachable = False
    for host, port in probe_hosts:
        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            reachable = True
            break
        except OSError:
            continue
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    with _ONLINE_LOCK:
        _ONLINE_CACHE["ts"] = time.monotonic()
        _ONLINE_CACHE["online"] = reachable
    return reachable


def should_search_kiwix(query: str) -> bool:
    """Return True if the query warrants an offline encyclopedia lookup."""
    if not isinstance(query, str):
        return False
    stripped = query.strip()
    if len(stripped) < 4:
        return False
    if stripped.startswith(("!", "/")):
        return False

    norm_greeting = re.sub(r"[!?.,:;]+$", "", stripped.lower()).strip()
    if norm_greeting in _GREETINGS_AND_ACKS:
        return False

    if _MESH_TELEMETRY_RE.search(stripped):
        return False

    return True


def extract_search_terms(query: str) -> tuple[str, list[str]]:
    """Extract search query string and relevance stems from natural language."""
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", (query or "").lower())
    relevance_terms = [t for t in tokens if len(t) > 1 and t not in _GLUE_STOP_WORDS]
    search_terms = [t for t in tokens if len(t) > 1 and t not in _FILLER_STOP_WORDS]
    if not search_terms:
        search_terms = relevance_terms or tokens
    return " ".join(search_terms[:5]), relevance_terms


def _stems_for(terms: list[str]) -> list[str]:
    return [t[:5] if len(t) >= 4 else t for t in terms]


def _kiwix_xml_search(
    base_url: str,
    pattern: str,
    limit: int = 10,
    timeout: float = 5.0,
) -> list[dict]:
    """Query kiwix-serve OpenSearch XML endpoint and parse full snippets."""
    if not pattern:
        return []
    url = f"{base_url.rstrip('/')}/search"
    resp = requests.get(
        url,
        params={"pattern": pattern, "pageLength": limit, "format": "xml"},
        timeout=timeout,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    hits = []
    for idx, item in enumerate(root.findall("./channel/item")):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        book = (item.findtext("book/title") or "").strip()
        desc_el = item.find("description")
        desc_raw = "".join(desc_el.itertext()) if desc_el is not None else ""
        snippet = re.sub(r"\s+", " ", desc_raw).strip()
        if title and link:
            hits.append({
                "rank": idx,
                "title": title,
                "link": link,
                "book": book,
                "snippet": snippet,
            })
    return hits


def _rank_hits(hits: list[dict], search_terms: list[str]) -> list[tuple[float, int, dict]]:
    """Deduplicate by title and rank Kiwix search hits by title/snippet relevance."""
    stems = _stems_for(search_terms)
    seen_titles = set()
    scored = []
    for hit in hits:
        norm_title = re.sub(r"\s+", " ", hit["title"].lower()).strip()
        if norm_title in seen_titles:
            continue
        seen_titles.add(norm_title)

        title_hits = sum(1 for s in stems if s in norm_title)
        snip_low = hit["snippet"].lower()
        snip_hits = sum(1 for s in stems if s in snip_low)
        score = (title_hits * 10.0) + (snip_hits * 2.0) - (hit["rank"] * 0.5)
        if "/quiz" in norm_title or "(disambiguation)" in norm_title:
            score -= 8.0
        book_low = hit["book"].lower()
        if any(b in book_low for b in ("wikimed", "post-disaster", "appropedia")):
            score += 0.3
        scored.append((score, title_hits, hit))

    scored.sort(key=lambda entry: entry[0], reverse=True)
    return scored


class _ArticleParagraphParser(HTMLParser):
    """Extract clean article paragraphs and list items while skipping boilerplate."""

    SKIP_TAGS = frozenset({
        "script", "style", "noscript", "nav", "header", "footer",
        "aside", "table", "sup", "figure", "svg",
    })
    HATNOTE_PREFIXES = (
        "not to be confused",
        "this article is about",
        "for other uses",
    )

    def __init__(self):
        super().__init__()
        self.skip_depth = 0
        self.in_body = False
        self.capture_tag = None
        self.current: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t == "body":
            self.in_body = True
        if t in self.SKIP_TAGS:
            self.skip_depth += 1
        elif self.in_body and self.skip_depth == 0 and t in ("p", "li") and self.capture_tag is None:
            self.capture_tag = t
            self.current = []

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in self.SKIP_TAGS and self.skip_depth > 0:
            self.skip_depth -= 1
        elif t == "body":
            self.in_body = False
        elif t == self.capture_tag:
            text = re.sub(r"\s+", " ", "".join(self.current)).strip()
            text = re.sub(r"\s+([.,;:!?])", r"\1", text)
            if len(text) >= 40 and not text.lower().startswith(self.HATNOTE_PREFIXES):
                self.paragraphs.append(text)
            self.capture_tag = None
            self.current = []

    def handle_data(self, data):
        if self.in_body and self.skip_depth == 0 and self.capture_tag is not None:
            self.current.append(data)


def _truncate_words(text: str, limit: int) -> str:
    """Truncate text cleanly at a word boundary when exceeding limit."""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    cut = text[: limit - 3].rsplit(" ", 1)[0].strip()
    if not cut:
        cut = text[: limit - 3].strip()
    return f"{cut}..."


def _extract_article_excerpt(
    base_url: str,
    link: str,
    relevance_terms: list[str],
    max_chars: int,
    timeout: float = 5.0,
) -> str:
    """Fetch a Kiwix article HTML page and extract lead + query-relevant paragraphs."""
    article_url = urljoin(f"{base_url.rstrip('/')}/", link.lstrip("/"))
    resp = requests.get(article_url, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.encoding or "utf-8"

    parser = _ArticleParagraphParser()
    parser.feed(resp.text)
    parser.close()
    paras = parser.paragraphs
    if not paras:
        return ""

    lead_cap = min(max_chars, min(360, max(200, max_chars // 2)))
    selected = [_truncate_words(paras[0], lead_cap)]
    used_indices = {0}

    stems = _stems_for(relevance_terms)
    scored_paras = []
    for idx, para in enumerate(paras[1:80], start=1):
        low = para.lower()
        hits = sum(1 for s in stems if s in low)
        if hits > 0:
            scored_paras.append((hits, -idx, idx, para))
    scored_paras.sort(reverse=True)

    for _, _, idx, para in scored_paras:
        if idx not in used_indices:
            selected.append(para)
            used_indices.add(idx)

    out = ""
    for para in selected:
        sep = " " if out else ""
        if len(out) + len(sep) + len(para) <= max_chars:
            out += sep + para
            continue
        remaining = max_chars - len(out) - len(sep)
        if remaining >= 80:
            out += sep + _truncate_words(para, remaining)
        break
    return out.strip()


def search_kiwix(
    query: str,
    base_url: str,
    max_results: int = 2,
    max_chars: int = 1200,
    timeout: float = 5.0,
) -> Optional[str]:
    """Search local Kiwix ZIM library and return formatted offline reference text."""
    if not base_url or not should_search_kiwix(query):
        return None

    pattern, relevance_terms = extract_search_terms(query)
    if not pattern:
        return None

    search_terms = pattern.split()
    try:
        hits = _kiwix_xml_search(base_url, pattern, limit=10, timeout=timeout)
        ranked = _rank_hits(hits, search_terms)

        if (not ranked or ranked[0][1] == 0) and len(search_terms) > 2:
            fallback_hits = _kiwix_xml_search(
                base_url,
                " ".join(search_terms[:2]),
                limit=8,
                timeout=timeout,
            )
            ranked = _rank_hits(hits + fallback_hits, search_terms)

        if not ranked:
            return None

        chosen = [ranked[0]]
        if (
            max_results > 1
            and len(ranked) > 1
            and (ranked[0][1] == 0 or (ranked[0][0] - ranked[1][0]) < 6.0)
        ):
            chosen = ranked[:max_results]

        per_hit_budget = max_chars if len(chosen) == 1 else max(250, max_chars // len(chosen))
        entries = []
        for _, _, hit in chosen:
            excerpt = ""
            try:
                excerpt = _extract_article_excerpt(
                    base_url,
                    hit["link"],
                    relevance_terms,
                    max_chars=per_hit_budget,
                    timeout=timeout,
                )
            except Exception as exc:
                logger.debug(f"Kiwix article fetch failed for {hit['link']}: {exc}")

            if not excerpt:
                excerpt = _truncate_words(hit["snippet"], per_hit_budget) if hit["snippet"] else ""

            if not excerpt:
                continue

            header = f"{hit['title']} ({hit['book']})" if hit["book"] else hit["title"]
            entries.append(f"{header}: {excerpt}")

        if not entries:
            return None

        body = "\n\n".join(entries)
        return f"[OFFLINE REFERENCE (Kiwix)]\n{body}\n[/OFFLINE REFERENCE]"
    except Exception as exc:
        logger.warning(f"⚠️ Kiwix lookup error for '{query[:50]}': {exc}")
        return None

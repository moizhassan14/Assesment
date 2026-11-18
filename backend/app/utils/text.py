"""Domain normalisation and brand-mention detection helpers (pure functions)."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)


def normalize_domain(raw: str) -> str:
    """``https://www.Asana.com/x`` → ``asana.com``. Raises ``ValueError`` when invalid."""
    text = (raw or "").strip().lower()
    if not text:
        raise ValueError("Domain is required")
    if "://" not in text:
        text = "http://" + text
    host = urlparse(text).hostname or ""
    host = host.rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not _DOMAIN_RE.match(host):
        raise ValueError("Value is not a valid domain name")
    return host


def domain_of_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def domain_matches(candidate: str | None, target_domain: str) -> bool:
    """True when ``candidate`` is the target domain or one of its subdomains."""
    if not candidate:
        return False
    candidate = candidate.lower().rstrip(".")
    if candidate.startswith("www."):
        candidate = candidate[4:]
    return candidate == target_domain or candidate.endswith("." + target_domain)



def mentions_entity(text: str | None, name: str, domain: str) -> bool:
    """Case-insensitive whole-word match on the entity name or its domain.

    The bare domain label (``monday`` for ``monday.com``) is deliberately NOT used:
    it produces false positives on common words.
    """
    if not text:
        return False
    haystack = text.lower()
    needles = {name.strip().lower(), domain.lower()}
    for needle in needles:
        if not needle:
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", haystack):
            return True
    return False


def extract_urls(text: str | None) -> list[str]:
    return _URL_RE.findall(text or "")


def normalize_query(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())

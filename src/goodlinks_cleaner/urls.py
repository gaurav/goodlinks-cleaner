"""URL helpers: host extraction, duplicate-detection normalisation, shorteners."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_HOST_PREFIX = re.compile(r"^(www\d*|m|amp|mobile)\.")

# Query parameters that never change which document a URL points at.
_TRACKING_PARAMS = re.compile(
    r"^(utm_\w+|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid|igshid|ref|ref_src|ref_url|source|"
    r"gift|giftId|unlocked_article_code|smid|share_type|cmpid|cmp|ncid|ocid|spm|sr_share|"
    r"__twitter_impression|_hsenc|_hsmi|hsCtaTracking|yclid|wt_mc|s|t|si|feature|cta|src)$",
    re.IGNORECASE,
)

# Hosts that only ever redirect somewhere else. pocket.co stopped resolving when
# Pocket shut down (2025), so those links are dead unless re-resolved elsewhere.
SHORTENER_HOSTS = {
    "pocket.co", "getpocket.com", "pca.st",
    "t.co", "bit.ly", "j.mp", "goo.gl", "ow.ly", "tinyurl.com", "buff.ly", "dlvr.it",
    "ift.tt", "econ.st", "nyti.ms", "wapo.st", "trib.al", "fb.me", "lnkd.in", "youtu.be",
    "amzn.to", "is.gd", "v.gd", "cutt.ly", "rb.gy", "shorturl.at", "bbc.in", "n.pr",
    "flip.it", "feedproxy.google.com", "feeds.feedburner.com",
}


def host(url: str) -> str:
    """Hostname with www./m./amp. stripped and lowercased. '' if unparsable."""
    try:
        h = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return _HOST_PREFIX.sub("", h.lower())


def is_shortener(url: str) -> bool:
    h = host(url)
    return h in SHORTENER_HOSTS or (urlsplit(url).hostname or "").lower() in SHORTENER_HOSTS


def normalize_url(url: str) -> str:
    """Canonical form for duplicate detection.

    Lowercases scheme and host, strips www./m./amp., drops tracking query
    parameters and the fragment, sorts the remaining query parameters, and
    removes a trailing slash. http and https collapse together. The result
    is a key, not necessarily a fetchable URL.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    h = host(url)
    path = re.sub(r"/+", "/", parts.path) or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    if path.endswith(("/index.html", "/index.htm", "/index.php")):
        path = path.rsplit("/", 1)[0] or "/"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not _TRACKING_PARAMS.match(k)]
    query.sort()
    return urlunsplit(("https", h, path, urlencode(query), ""))

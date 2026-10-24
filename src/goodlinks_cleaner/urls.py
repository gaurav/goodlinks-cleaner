"""URL helpers: host extraction, duplicate-detection normalisation, shorteners."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_HOST_PREFIX = re.compile(r"^(www\d*|m|amp|mobile)\.")

# Query parameters that never change which document a URL points at. Built
# from a census of this library (see `glc dupes --params`); publisher-specific
# ones are noted. Anything not listed is kept, so unknown sites stay safe.
_TRACKING_PREFIXES = ("utm_", "wt.", "wa_", "sp", "sfmc_", "mc_", "pk_", "hsa_", "ns_", "_hs", "et_")
_TRACKING_PARAMS = {
    # generic
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "yclid", "mkt_tok", "mkt-key",
    "ref", "ref_src", "ref_url", "source", "src", "cta", "amp", "campaign_id", "cid",
    "newsletter", "nlcode", "sponsored", "position", "triedredirect",
    # economist.com
    "cid1", "etear", "fsrc", "frsc",
    # newyorker.com / condé nast
    "bxid", "cndid", "esrc", "hasha", "hashb", "hashc", "mbid", "user_id",
    # nytimes.com
    "_r", "emc", "nl", "te", "regi_id", "segment_id", "instance_id", "smid", "smtyp",
    "mtrref", "gwh", "gwt", "unlocked_article_code", "gift", "giftid",
    # nature.com
    "sap-outbound-id",
    # theatlantic.com
    "silverid", "silverid-ref",
    # washingtonpost.com / guardian / vox / unc.edu mailings
    "wpisrc", "wpmm", "cmp", "ueid", "j", "l", "mid", "jb",
}


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k in _TRACKING_PARAMS or k.startswith(_TRACKING_PREFIXES)


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
    """Hostname with www./m./amp. stripped and lowercased. '' if unparsable.

    Accepts a bare hostname too, so CLI filters like --host www.example.com work.
    """
    if "://" not in url and "/" not in url and "." in url:
        url = "https://" + url
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
             if not _is_tracking(k)]
    query.sort()
    return urlunsplit(("https", h, path, urlencode(query), ""))

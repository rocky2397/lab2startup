"""Best-effort region inference from affiliation strings."""

from __future__ import annotations

import re

_COUNTRY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(united states|usa|u\.s\.a\.|u\.s\.)\b", re.I), "United States"),
    (re.compile(r"\b(united kingdom|uk|u\.k\.)\b", re.I), "United Kingdom"),
    (re.compile(r"\b(canada)\b", re.I), "Canada"),
    (re.compile(r"\b(germany|deutschland|munich|münchen)\b", re.I), "Germany"),
    (re.compile(r"\b(france|paris)\b", re.I), "France"),
    (re.compile(r"\b(switzerland|zurich|zürich)\b", re.I), "Switzerland"),
    (re.compile(r"\b(netherlands|holland|amsterdam)\b", re.I), "Netherlands"),
    (re.compile(r"\b(spain|barcelona|madrid)\b", re.I), "Spain"),
    (re.compile(r"\b(italy|milano|milan)\b", re.I), "Italy"),
    (re.compile(r"\b(sweden|stockholm)\b", re.I), "Sweden"),
    (re.compile(r"\b(norway|oslo)\b", re.I), "Norway"),
    (re.compile(r"\b(denmark|copenhagen)\b", re.I), "Denmark"),
    (re.compile(r"\b(finland|helsinki)\b", re.I), "Finland"),
    (re.compile(r"\b(austria|vienna)\b", re.I), "Austria"),
    (re.compile(r"\b(belgium|brussels)\b", re.I), "Belgium"),
    (re.compile(r"\b(ireland|dublin)\b", re.I), "Ireland"),
    (re.compile(r"\b(poland|warsaw)\b", re.I), "Poland"),
    (re.compile(r"\b(czech|prague)\b", re.I), "Czech Republic"),
    (re.compile(r"\b(israel)\b", re.I), "Israel"),
    (re.compile(r"\b(singapore)\b", re.I), "Singapore"),
    (
        re.compile(
            r"\b(china|beijing|shanghai|shenzhen|harbin|tsinghua|peking|zhejiang|hangzhou|guangzhou|heilongjiang)\b",
            re.I,
        ),
        "China",
    ),
    (re.compile(r"\b(japan|tokyo)\b", re.I), "Japan"),
    (re.compile(r"\b(south korea|korea|seoul)\b", re.I), "South Korea"),
    (re.compile(r"\b(india|bangalore|bengaluru)\b", re.I), "India"),
    (re.compile(r"\b(australia)\b", re.I), "Australia"),
]

_US_INSTITUTION_HINTS = (
    "stanford",
    "mit ",
    " mit",
    "berkeley",
    "carnegie mellon",
    "cmu",
    "harvard",
    "princeton",
    "yale",
    "cornell",
    "georgia tech",
    "caltech",
    "ucla",
    "usc",
    "columbia",
    "nyu",
    "microsoft",
    "google",
    "meta ",
    "openai",
    "deepmind",
)


def infer_region_hint(affiliation: str | None) -> str | None:
    """Best-effort region/country label from an affiliation string."""
    if not affiliation or not affiliation.strip():
        return None

    text = affiliation.strip()
    for pattern, label in _COUNTRY_PATTERNS:
        if pattern.search(text):
            return label

    lowered = text.lower()
    if any(hint in lowered for hint in _US_INSTITUTION_HINTS):
        return "United States"
    if "university of oxford" in lowered or "university of cambridge" in lowered:
        return "United Kingdom"
    if "eth zurich" in lowered or "epfl" in lowered:
        return "Switzerland"

    return None

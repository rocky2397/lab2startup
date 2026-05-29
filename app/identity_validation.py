"""Validate that Perplexity profile/signal text refers to the intended researcher."""

from __future__ import annotations

import re
import unicodedata

_LEADING_SUBJECT_RE = re.compile(
    r"^([A-Z][\w\u00C0-\u024F]+(?:\s+[A-Z][\w\u00C0-\u024F]+){0,3})\s+"
    r"(?:is|was|has|are|works|worked|studied|joined|leads|founded|co-founded|specializes|specialises)\b",
    re.UNICODE,
)


def _normalize_token(token: str) -> str:
    nfkd = unicodedata.normalize("NFKD", token.lower())
    return "".join(char for char in nfkd if not unicodedata.combining(char))


def researcher_name_tokens(name: str) -> list[str]:
    """Split a display name into normalized tokens (handles unicode accents)."""
    cleaned = re.sub(r"[^a-zA-Z\s\u00C0-\u024F]", " ", name)
    return [_normalize_token(token) for token in cleaned.split() if len(token) >= 2]


def names_plausibly_same(target_name: str, other_name: str) -> bool:
    """Return True when two names likely refer to the same person (first/last overlap)."""
    target = researcher_name_tokens(target_name)
    other = researcher_name_tokens(other_name)
    if not target or not other:
        return False

    target_first, target_last = target[0], target[-1]
    other_first, other_last = other[0], other[-1]
    if target_first == other_first and target_last == other_last:
        return True

    target_set = set(target)
    other_set = set(other)
    return target_last in other_set and target_first in other_set


def extract_leading_subject_name(text: str) -> str | None:
    """Extract a leading subject name from prose, e.g. 'Xingang Peng is a prominent...'."""
    stripped = text.strip()
    if not stripped:
        return None
    match = _LEADING_SUBJECT_RE.match(stripped)
    if not match:
        return None
    return match.group(1).strip()


def text_refers_to_different_person(target_name: str, text: str) -> bool:
    """Return True when text clearly names a different person at the start."""
    subject = extract_leading_subject_name(text)
    if subject is None:
        return False
    return not names_plausibly_same(target_name, subject)


def signal_description_matches_researcher(researcher_name: str, description: str) -> bool:
    """Return True when a signal description plausibly refers to the target researcher."""
    if not description.strip():
        return False
    return not text_refers_to_different_person(researcher_name, description)


def profile_identity_matches_researcher(researcher_name: str, profile: dict) -> bool:
    """Return True when profile identity text plausibly refers to the target researcher."""
    explanation = str(profile.get("identity_explanation") or "").strip()
    if explanation and text_refers_to_different_person(researcher_name, explanation):
        return False
    return True

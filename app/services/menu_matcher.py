"""
menu_matcher.py — server-side reconciliation of spoken item names to real menu rows.

Why this exists
---------------
The prompt asks the LLM to "aggressively find the closest matching item" and to
always emit the exact English menu name. That is an unreliable contract: speech
-to-text garbles Indian dish names constantly ("murabaji warehouse" -> "milagai
bajji"), and when the model guesses wrong the frontend receives an `add_to_cart`
for an item that does not exist, silently drops it, and the customer hears
"Added 2 Masala Dosa" while their cart stays empty. That mismatch is exactly
what makes the agent feel like it is fumbling.

So we re-check every item name the model produced against the actual menu
before it leaves the backend, using a phonetic-ish normalisation plus fuzzy
ratio. Anything we cannot confidently resolve is dropped from ui_actions and
reported back so the caller can correct the spoken reply.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

# Minimum similarity for an automatic match. Tuned so that "masala dosaa",
# "masal dosa" and "masala thosai" all resolve, while "pizza" does not latch
# onto an unrelated dish.
_AUTO_ACCEPT = 0.82
# Below this we treat the item as genuinely unavailable. Deliberately strict:
# quietly turning "chicken biryani" into "Veg Biryani" is far worse than saying
# we don't serve it.
_REJECT_BELOW = 0.74

# This is a pure-vegetarian restaurant. If the customer explicitly names a
# non-veg ingredient we must never fuzzy-match it onto a veg dish - the token
# overlap on "biryani"/"noodles" is otherwise high enough to sail through.
_NON_VEG_TOKENS = {
    "chicken", "mutton", "beef", "pork", "egg", "eggs", "omelette", "omlet",
    "fish", "prawn", "prawns", "shrimp", "crab", "meat", "bacon", "ham",
    "keema", "kheema", "chikken", "chiken",
}

# Common speech-to-text and transliteration variants seen in South Indian
# restaurant audio. Applied before fuzzy matching.
_PHONETIC_RULES: list[tuple[str, str]] = [
    (r"\bthosai\b|\bdosai\b|\bdhosa\b|\bthosa\b", "dosa"),
    (r"\bidly\b|\bidlee\b|\biddali\b|\biddly\b|\bidili\b", "idli"),
    (r"\bvadai\b|\bwada\b|\bvadda\b|\bwadai\b", "vada"),
    (r"\bghee\b|\bghi\b|\bgee\b", "ghee"),
    (r"\bcofee\b|\bcoffe\b|\bkaapi\b|\bkapi\b|\bcopy\b", "coffee"),
    (r"\bsambhar\b|\bsambaar\b|\bsaambar\b", "sambar"),
    (r"\buthappam\b|\buthapam\b|\boothappam\b", "uttapam"),
    (r"\bpongle\b|\bpongel\b", "pongal"),
    (r"\bbajji\b|\bbaji\b|\bbhaji\b", "bajji"),
    (r"\bbonda\b|\bbondha\b", "bonda"),
    (r"\bchutni\b|\bchutny\b", "chutney"),
    (r"\bparota\b|\bporotta\b|\bparatha\b", "parotta"),
    (r"\bbriyani\b|\bbiriyani\b|\bbiryani\b", "biryani"),
    (r"\bpaneer\b|\bpanner\b|\bpanir\b", "paneer"),
    (r"\bmanchuria\b|\bmanchurian\b|\bmanshooryan\b", "manchurian"),
    (r"\bkesari\b|\bkesaribath\b", "kesari"),
    (r"\brava\b|\brawa\b|\bravaa\b", "rava"),
    (r"\bmasal\b|\bmasaala\b|\bmasalla\b", "masala"),
    (r"\bfilter\b|\bphilter\b", "filter"),
    (r"\brost\b|\broste\b|\broasted\b", "roast"),
    (r"\bnoodels\b|\bnoodels\b|\bnudles\b", "noodles"),
]

# Noise words that carry no identifying signal for a dish.
_STOPWORDS = {
    "a", "an", "the", "one", "two", "three", "plate", "plates", "piece",
    "pieces", "please", "pls", "order", "add", "want", "need", "give",
    "me", "my", "of", "and", "with", "some", "hot", "fresh", "full",
    "half", "set", "nos", "no",
}


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


@lru_cache(maxsize=2048)
def normalize(name: str) -> str:
    """Collapse a dish name to a comparable canonical form."""
    if not name:
        return ""
    text = _strip_accents(str(name).lower().strip())
    # Drop anything that is not a letter, digit or space (₹, (), -, etc.)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    for pattern, replacement in _PHONETIC_RULES:
        text = re.sub(pattern, replacement, text)

    tokens = [t for t in text.split() if t not in _STOPWORDS]
    # Collapse doubled letters ("dosaa" -> "dosa", "iddli" -> "idli") which is
    # the single most common STT artefact for these dish names.
    tokens = [re.sub(r"(.)\1+", r"\1", t) for t in tokens]
    return " ".join(sorted(tokens)) if tokens else text


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    base = SequenceMatcher(None, a, b).ratio()

    # Token overlap rewards "ghee roast" matching "ghee roast dosa" even though
    # raw character ratio is diluted by the extra word.
    a_tokens, b_tokens = set(a.split()), set(b.split())
    if a_tokens and b_tokens:
        overlap = len(a_tokens & b_tokens) / len(a_tokens | b_tokens)
        containment = len(a_tokens & b_tokens) / min(len(a_tokens), len(b_tokens))
        return max(base, (base + overlap) / 2, containment * 0.95)
    return base


def match_item(spoken_name: str, menu_names: list[str]) -> tuple[str | None, float]:
    """Resolve a spoken/garbled name to an exact menu name.

    Returns (matched_name, confidence). matched_name is None when nothing on the
    menu is a plausible match, which the caller should treat as "unavailable".
    """
    if not spoken_name or not menu_names:
        return None, 0.0

    target = normalize(spoken_name)
    if not target:
        return None, 0.0

    # Hard reject explicit non-veg requests before fuzzy matching, so we never
    # substitute a veg dish for something the customer did not ask for.
    requested_tokens = set(target.split())
    if requested_tokens & _NON_VEG_TOKENS:
        return None, 0.0

    best_name: str | None = None
    best_score = 0.0

    for candidate in menu_names:
        score = _similarity(target, normalize(candidate))
        if score > best_score:
            best_score, best_name = score, candidate
            if score == 1.0:
                break

    if best_score < _REJECT_BELOW:
        return None, best_score
    return best_name, best_score


def resolve_cart_actions(
    ui_actions: list[dict], menu_names: list[str]
) -> tuple[list[dict], list[str]]:
    """Rewrite add_to_cart actions to use exact menu names.

    Returns (cleaned_actions, unresolved_names). Any add_to_cart whose item
    cannot be matched is removed so the frontend never receives a phantom item.
    """
    if not ui_actions:
        return [], []

    cleaned: list[dict] = []
    unresolved: list[str] = []

    for action in ui_actions:
        if not isinstance(action, dict):
            continue

        if action.get("action") != "add_to_cart":
            cleaned.append(action)
            continue

        spoken = action.get("item") or action.get("name") or ""
        matched, score = match_item(spoken, menu_names)

        if matched is None:
            unresolved.append(str(spoken))
            continue

        updated = dict(action)
        updated["item"] = matched
        if score < _AUTO_ACCEPT:
            # Still plausible, but flag it so the UI/analytics can see that the
            # backend had to stretch to make this match.
            updated["match_confidence"] = round(score, 3)
        cleaned.append(updated)

    return cleaned, unresolved





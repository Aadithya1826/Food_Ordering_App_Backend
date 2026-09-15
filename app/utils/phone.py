"""
Central phone normalization utility.
All Indian customer phone numbers are normalized to the +91XXXXXXXXXX format.
This prevents duplicate customer rows caused by different representations
of the same number (e.g. 9876543210 vs +91 98765 43210 vs +919876543210).
"""
import re


def normalize_phone(raw: str) -> str:
    """
    Normalize an Indian mobile number to +91XXXXXXXXXX format.

    Examples:
        9876543210          -> +919876543210
        09876543210         -> +919876543210
        +91 98765 43210     -> +919876543210
        +919876543210       -> +919876543210
        91 9876543210       -> +919876543210

    Returns the normalized string, or the stripped raw value if it cannot
    be recognized as a 10-digit Indian number (so we don't silently drop
    international numbers that might be stored in the DB already).
    """
    if not raw:
        return ""

    # Strip whitespace and common separators
    stripped = re.sub(r"[\s\-\(\)]+", "", raw.strip())

    # Remove leading country code variations
    # +919876543210 -> 9876543210
    # 919876543210  -> 9876543210
    # 09876543210   -> 9876543210
    digits_only = re.sub(r"[^\d]", "", stripped)

    if len(digits_only) == 12 and digits_only.startswith("91"):
        digits_only = digits_only[2:]  # strip country code
    elif len(digits_only) == 11 and digits_only.startswith("0"):
        digits_only = digits_only[1:]  # strip leading 0

    if len(digits_only) == 10 and digits_only[0] in "6789":
        return f"+91{digits_only}"

    # Fallback: return the stripped value as-is (handle unusual inputs gracefully)
    return stripped


def is_valid_indian_mobile(raw: str) -> bool:
    """Returns True if the normalized number is a valid 10-digit Indian mobile."""
    normalized = normalize_phone(raw)
    digits = re.sub(r"[^\d]", "", normalized)
    return len(digits) == 12 and digits.startswith("91") and digits[2] in "6789"

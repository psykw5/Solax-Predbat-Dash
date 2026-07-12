"""Redaction helpers for user-facing reports."""

from __future__ import annotations

import hashlib
import re


def redact_sensitive_text(value: object) -> str:
    """Redact obvious personal/account identifiers from metadata text."""
    text = "" if value is None else str(value)
    text = re.sub(r"[\w.+-]+@[\w.-]+", "[REDACTED_EMAIL]", text)
    text = re.sub(r"\b[A-Z0-9]{8,}\b", "[REDACTED_ID]", text, flags=re.IGNORECASE)
    return text


def text_hash(value: object) -> str:
    text = "" if value is None else str(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

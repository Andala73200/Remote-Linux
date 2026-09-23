from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path


LOCALES_DIR = Path(__file__).resolve().parent / "locales"
DEFAULT_LANGUAGE = "en"
LANGUAGE_CHOICES = (
    ("fr", "Français"),
    ("en", "English"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("it", "Italiano"),
    ("pt", "Português"),
    ("nl", "Nederlands"),
    ("pl", "Polski"),
    ("ru", "Русский"),
    ("uk", "Українська"),
    ("cs", "Čeština"),
    ("ro", "Română"),
    ("sv", "Svenska"),
    ("no", "Norsk"),
    ("tr", "Türkçe"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("zh", "中文"),
)
CATALOG_LANGUAGES = tuple(code for code, _label in LANGUAGE_CHOICES)
SUPPORTED_LANGUAGES = {"auto", *CATALOG_LANGUAGES}


@lru_cache(maxsize=None)
def load_catalog(language: str) -> dict[str, dict[str, str]]:
    path = LOCALES_DIR / f"{language}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        payload = {}
    return {
        "strings": dict(payload.get("strings", {})),
        "fragments": dict(payload.get("fragments", {})),
    }


def load_catalogs() -> dict[str, dict[str, dict[str, str]]]:
    return {language: load_catalog(language) for language in CATALOG_LANGUAGES}


def reverse_index(
    catalogs: dict[str, dict[str, dict[str, str]]], section: str
) -> dict[str, str]:
    reverse: dict[str, str] = {}
    for language in CATALOG_LANGUAGES:
        for key, value in catalogs[language][section].items():
            reverse.setdefault(value, key)
    return reverse


def translate_from_catalogs(
    value: object,
    language: str,
    catalogs: dict[str, dict[str, dict[str, str]]],
    string_reverse: dict[str, str],
    fragment_reverse: dict[str, str],
    fragments: bool = True,
) -> str:
    text = str(value or "")
    if not text:
        return text
    target = catalogs.get(language, catalogs[DEFAULT_LANGUAGE])
    fallback = catalogs[DEFAULT_LANGUAGE]

    if text.startswith("ui."):
        if text in fallback["strings"]:
            return target["strings"].get(text, fallback["strings"][text])
        return text
    if text.startswith("fragment."):
        if fragments and text in fallback["fragments"]:
            return target["fragments"].get(text, fallback["fragments"][text])
        return text

    key = string_reverse.get(text)
    if key:
        return target["strings"].get(key, fallback["strings"].get(key, text))
    if not fragments:
        return text
    translated = text
    replacements: list[tuple[str, str]] = []
    for source, key in fragment_reverse.items():
        target_value = target["fragments"].get(
            key, fallback["fragments"].get(key, source)
        )
        # Keep identity entries too: a longer unchanged fragment must shield
        # its text from a shorter fragment in another language (for example
        # English ``timer(s)`` versus Italian ``timer``).
        replacements.append((source, target_value))
    if not replacements:
        return translated

    # Apply fragment translations in one pass. Sequential str.replace calls
    # can translate text produced by an earlier replacement a second time
    # (for example ``timer(s)`` becoming ``timer(s)(s)`` or a profile name
    # accidentally gaining the translation of another short fragment).
    mapping = dict(replacements)
    pattern = re.compile(
        "|".join(
            re.escape(source)
            for source in sorted(mapping, key=len, reverse=True)
        )
    )
    return pattern.sub(lambda match: mapping[match.group(0)], translated)

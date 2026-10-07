"""Tests for parsing/normalize.py: text normalization and language code normalization."""

import pytest

from spott.ingest.parsing.normalize import normalize_lang


@pytest.mark.parametrize(
    "raw_lang,fallback_text,expected",
    [
        ("ru-RU", "", "ru"),
        ("ru_RU", "", "ru"),
        ("ro-RO", "", "ro"),
        ("ro-MD", "", "ro"),
        ("en-US", "", "en"),
        ("en-GB", "", "en"),
        ("uk-UA", "", "uk"),
        ("ru", "", "ru"),
        ("ro", "", "ro"),
        ("en", "", "en"),
        ("uk", "", "uk"),
        ("ron", "", "ro"),
        ("rus", "", "ru"),
        ("eng", "", "en"),
        ("ukr", "", "uk"),
        # Empty or None with fallback text
        ("", "Текст на русском языке для определения языка.", "ru"),
        (None, "Text redactat în limba română pentru cetățenii municipiului.", "ro"),
        (None, "English instructions for international visitors and citizens.", "en"),
        (None, "Офіційне повідомлення українською мовою для біженців.", "uk"),
        # Empty without text defaults to 'ro'
        ("", "", "ro"),
        (None, "", "ro"),
    ],
)
def test_normalize_lang(raw_lang, fallback_text, expected):
    assert normalize_lang(raw_lang, fallback_text=fallback_text) == expected


def test_chunker_uses_normalized_lang():
    from spott.ingest.chunking.chunker import chunk_document

    doc = {
        "sha256": "lang_norm_test",
        "metadata": {
            "title": "Document cu limba regională",
            "lang": "ru-RU",
        },
        "blocks": [
            {
                "id": 0,
                "type": "paragraph",
                "text": "Это текст официального документа на русском языке.",
                "lang": "ru-RU",
                "section": [],
            }
        ],
    }
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert chunks[0]["lang"] == "ru"

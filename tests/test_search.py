"""Tests for local search module."""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.search import (
    search, get_stats, SearchResult, expand_icelandic_query,
    is_phrase_query, build_phrase_query,
)


@pytest.fixture
def mock_db():
    """Create a temporary test database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(db_path)

    # Create tables
    conn.execute("""
        CREATE TABLE verdicts (
            id INTEGER PRIMARY KEY,
            court TEXT NOT NULL,
            case_number TEXT NOT NULL,
            filename TEXT NOT NULL,
            text_length INTEGER,
            verdict_url TEXT
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE verdicts_fts USING fts5(
            case_number,
            content
        )
    """)

    # Insert test data
    test_cases = [
        ("landsrettur", "123/2024", "123_2024.pdf", "Umgengisrettur foreldra og barna"),
        ("haestirettur", "456/2023", "456_2023.txt", "Skadabaetur vegna umferdarslyss"),
        ("heradsdomstolar", "789/2022", "789_2022.pdf", "Vinnuslys a verkstad"),
    ]

    for court, case_num, filename, content in test_cases:
        cursor = conn.execute(
            "INSERT INTO verdicts (court, case_number, filename, text_length) VALUES (?, ?, ?, ?)",
            (court, case_num, filename, len(content))
        )
        conn.execute(
            "INSERT INTO verdicts_fts (rowid, case_number, content) VALUES (?, ?, ?)",
            (cursor.lastrowid, case_num, content)
        )

    conn.commit()
    conn.close()

    yield db_path

    db_path.unlink()


def test_search_returns_results(mock_db):
    """Test that search returns matching results."""
    with patch("app.search.DB_PATH", mock_db):
        results = search("umgengisrettur")

    assert len(results) == 1
    assert results[0].case_number == "123/2024"
    assert results[0].court == "landsrettur"


def test_search_empty_query(mock_db):
    """Test that empty query returns no results."""
    with patch("app.search.DB_PATH", mock_db):
        results = search("")
        assert results == []

        results = search("   ")
        assert results == []


def test_search_no_matches(mock_db):
    """Test that unmatched query returns empty list."""
    with patch("app.search.DB_PATH", mock_db):
        results = search("nonexistent term xyz123")

    assert results == []


def test_get_stats(mock_db):
    """Test index statistics."""
    with patch("app.search.DB_PATH", mock_db):
        stats = get_stats()

    assert stats["total"] == 3
    assert stats["by_court"]["landsrettur"] == 1
    assert stats["by_court"]["haestirettur"] == 1
    assert stats["by_court"]["heradsdomstolar"] == 1


def test_search_result_dataclass():
    """Test SearchResult dataclass."""
    result = SearchResult(
        id=1,
        court="landsrettur",
        court_display="Landsrettur",
        case_number="123/2024",
        snippet="Test snippet",
        url="https://example.com"
    )

    assert result.id == 1
    assert result.court == "landsrettur"
    assert result.case_number == "123/2024"


def test_search_multi_word_or_matching(mock_db):
    """Multi-word queries match documents containing ANY of the words."""
    with patch("app.search.DB_PATH", mock_db):
        # "umgengisrettur" is in doc 1, "vinnuslys" is in doc 3
        results = search("umgengisrettur vinnuslys")

    assert len(results) == 2
    case_numbers = {r.case_number for r in results}
    assert "123/2024" in case_numbers  # umgengisrettur
    assert "789/2022" in case_numbers  # vinnuslys


class TestQuotationMarkHandling:
    """Test that search queries with quotation marks don't crash."""

    def test_expand_query_strips_quotes(self):
        """Quotation marks in query should not produce invalid FTS5 syntax."""
        result = expand_icelandic_query('"skadabaetur"')
        # Should not contain raw quotes that break FTS5
        assert '"' not in result

    def test_expand_query_strips_quotes_multi_word(self):
        """Multi-word quoted queries should also be handled."""
        result = expand_icelandic_query('"vegna tjons"')
        assert '"' not in result

    def test_search_with_quotes_does_not_crash(self, mock_db):
        """Searching with quotation marks should return results, not crash."""
        with patch("app.search.DB_PATH", mock_db):
            # Should not raise an exception
            results = search('"umgengisrettur"')
        assert isinstance(results, list)

    def test_search_with_single_quotes(self, mock_db):
        """Single quotes in search should not crash."""
        with patch("app.search.DB_PATH", mock_db):
            results = search("'umgengisrettur'")
        assert isinstance(results, list)

    def test_search_with_unbalanced_quotes(self, mock_db):
        """Unbalanced quotes should not crash."""
        with patch("app.search.DB_PATH", mock_db):
            results = search('"umgengisrettur')
        assert isinstance(results, list)

    def test_smart_quotes_stripped(self):
        """Smart/curly quotes (macOS auto-converts) must be stripped."""
        # Left/right double smart quotes
        result = expand_icelandic_query('\u201cskadabaetur\u201d')
        assert '\u201c' not in result
        assert '\u201d' not in result
        assert '"' not in result

    def test_icelandic_low_high_quotes_stripped(self):
        """Icelandic „..." quotation marks must be stripped."""
        result = expand_icelandic_query('\u201eskadabaetur\u201c')
        assert '\u201e' not in result
        assert '\u201c' not in result

    def test_smart_single_quotes_stripped(self):
        """Smart single quotes must be stripped."""
        result = expand_icelandic_query('\u2018skadabaetur\u2019')
        assert '\u2018' not in result
        assert '\u2019' not in result

    def test_search_with_smart_quotes_returns_results(self, mock_db):
        """Searching with smart quotes should find results, not fail."""
        with patch("app.search.DB_PATH", mock_db):
            results = search('\u201cumgengisrettur\u201d')
        assert len(results) == 1
        assert results[0].case_number == "123/2024"


@pytest.fixture
def phrase_db():
    """Create a test database with data for phrase search testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE verdicts (
            id INTEGER PRIMARY KEY,
            court TEXT NOT NULL,
            case_number TEXT NOT NULL,
            filename TEXT NOT NULL,
            text_length INTEGER,
            verdict_url TEXT
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE verdicts_fts USING fts5(
            case_number,
            content
        )
    """)

    # Doc 1: has "elisabet" but NOT "petursdottir"
    # Doc 2: has "petursdottir" but NOT "elisabet"
    # Doc 3: has the exact phrase "elisabet petursdottir"
    test_cases = [
        ("landsrettur", "100/2024", "100_2024.pdf",
         "elisabet jonsdottir var logmadur stefnanda"),
        ("landsrettur", "200/2024", "200_2024.pdf",
         "anna petursdottir hielt malflutning"),
        ("landsrettur", "300/2024", "300_2024.pdf",
         "elisabet petursdottir var logmadur i malinu"),
    ]

    for court, case_num, filename, content in test_cases:
        cursor = conn.execute(
            "INSERT INTO verdicts (court, case_number, filename, text_length) VALUES (?, ?, ?, ?)",
            (court, case_num, filename, len(content)),
        )
        conn.execute(
            "INSERT INTO verdicts_fts (rowid, case_number, content) VALUES (?, ?, ?)",
            (cursor.lastrowid, case_num, content),
        )

    conn.commit()
    conn.close()
    yield db_path
    db_path.unlink()


class TestPhraseSearch:
    """Test exact phrase matching when query is wrapped in quotes."""

    def test_is_phrase_query_ascii_double(self):
        assert is_phrase_query('"elisabet petursdottir"') is True

    def test_is_phrase_query_smart_quotes(self):
        assert is_phrase_query('\u201celisabet petursdottir\u201d') is True

    def test_is_phrase_query_icelandic_quotes(self):
        assert is_phrase_query('\u201eelisabet petursdottir\u201c') is True

    def test_is_phrase_query_single_quotes(self):
        assert is_phrase_query("'elisabet petursdottir'") is True

    def test_is_phrase_query_unquoted(self):
        assert is_phrase_query('elisabet petursdottir') is False

    def test_is_phrase_query_single_word(self):
        assert is_phrase_query('"elisabet"') is True

    def test_is_phrase_query_unbalanced(self):
        assert is_phrase_query('"elisabet petursdottir') is False

    def test_build_phrase_query_simple(self):
        result = build_phrase_query('"elisabet petursdottir"')
        # Should produce FTS5 phrase with ASCII double quotes
        assert '"elisabet petursdottir"' in result

    def test_build_phrase_query_with_variants(self):
        # "skadabaetur" has ð/d and æ/ae variants
        result = build_phrase_query('"skadabaetur vegna"')
        # Should contain multiple phrase variants ORed together
        assert '"' in result
        assert "OR" in result
        # Should include the original and the Icelandic variant
        assert "skadabaetur" in result or "skaðabætur" in result

    def test_phrase_search_exact_match_only(self, phrase_db):
        """Quoted phrase should only match documents with exact phrase."""
        with patch("app.search.DB_PATH", phrase_db):
            results = search('"elisabet petursdottir"')

        # Should ONLY match doc 3 (exact phrase), not docs 1 or 2
        assert len(results) == 1
        assert results[0].case_number == "300/2024"

    def test_phrase_search_smart_quotes(self, phrase_db):
        """Smart quotes should also trigger phrase search."""
        with patch("app.search.DB_PATH", phrase_db):
            results = search('\u201celisabet petursdottir\u201d')

        assert len(results) == 1
        assert results[0].case_number == "300/2024"

    def test_unquoted_search_matches_multiple(self, phrase_db):
        """Without quotes, search should match all docs with either word."""
        with patch("app.search.DB_PATH", phrase_db):
            results = search('elisabet petursdottir')

        # OR matching: all 3 docs have at least one of the words
        assert len(results) == 3

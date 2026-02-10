"""Tests for local search module."""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.search import search, get_stats, SearchResult


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
            text_length INTEGER
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

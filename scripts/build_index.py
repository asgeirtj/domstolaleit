#!/usr/bin/env python3
"""Build SQLite FTS5 search index from downloaded court documents."""

import re
import sqlite3
from pathlib import Path

import pdfplumber

DATA_DIR = Path(__file__).parent.parent / "data"
PDF_DIR = DATA_DIR / "pdfs"
DB_PATH = DATA_DIR / "verdicts.db"

COURTS = ["landsrettur", "heradsdomstolar", "haestirettur"]


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract text from PDF file."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text_parts = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            return " ".join(text_parts)
    except Exception as e:
        print(f"  Error extracting {pdf_path.name}: {e}")
        return ""


def extract_case_number(filename: str) -> str:
    """Extract case number from filename like S-123_2024.pdf -> S-123/2024."""
    name = filename.replace(".pdf", "").replace(".txt", "")
    # Convert underscore back to slash
    match = re.match(r"([A-Z]?-?\d+)_(\d{4})", name)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    return name


def init_db(conn: sqlite3.Connection):
    """Initialize database with FTS5 table."""
    conn.execute("DROP TABLE IF EXISTS verdicts")
    conn.execute("DROP TABLE IF EXISTS verdicts_fts")

    # Main table for metadata
    conn.execute("""
        CREATE TABLE verdicts (
            id INTEGER PRIMARY KEY,
            court TEXT NOT NULL,
            case_number TEXT NOT NULL,
            filename TEXT NOT NULL,
            text_length INTEGER,
            UNIQUE(court, filename)
        )
    """)

    # FTS5 table for full-text search
    conn.execute("""
        CREATE VIRTUAL TABLE verdicts_fts USING fts5(
            case_number,
            content,
            content_rowid='id'
        )
    """)

    conn.commit()


def index_court(conn: sqlite3.Connection, court: str):
    """Index all documents from a court."""
    court_dir = PDF_DIR / court
    if not court_dir.exists():
        print(f"  Directory not found: {court_dir}")
        return 0

    # Get file extension based on court
    if court == "haestirettur":
        files = list(court_dir.glob("*.txt"))
    else:
        files = list(court_dir.glob("*.pdf"))

    print(f"  Found {len(files)} files")

    indexed = 0
    for i, file_path in enumerate(files):
        if (i + 1) % 100 == 0:
            print(f"  Processing {i + 1}/{len(files)}...")
            conn.commit()

        case_number = extract_case_number(file_path.name)

        # Extract text
        if file_path.suffix == ".txt":
            text = file_path.read_text(encoding="utf-8")
        else:
            text = extract_text_from_pdf(file_path)

        if not text or len(text) < 50:
            continue

        # Insert into main table
        cursor = conn.execute("""
            INSERT OR IGNORE INTO verdicts (court, case_number, filename, text_length)
            VALUES (?, ?, ?, ?)
        """, (court, case_number, file_path.name, len(text)))

        if cursor.rowcount > 0:
            # Insert into FTS table
            row_id = cursor.lastrowid
            conn.execute("""
                INSERT INTO verdicts_fts (rowid, case_number, content)
                VALUES (?, ?, ?)
            """, (row_id, case_number, text))
            indexed += 1

    conn.commit()
    return indexed


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Building search index at {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    total = 0
    for court in COURTS:
        print(f"\nIndexing {court}...")
        count = index_court(conn, court)
        print(f"  Indexed {count} documents")
        total += count

    # Create additional indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_court ON verdicts(court)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_case_number ON verdicts(case_number)")
    conn.commit()

    print(f"\nTotal indexed: {total} documents")
    print(f"Database size: {DB_PATH.stat().st_size / 1024 / 1024:.1f} MB")

    conn.close()


if __name__ == "__main__":
    main()

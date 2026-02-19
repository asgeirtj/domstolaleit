#!/usr/bin/env python3
"""Build SQLite FTS5 search index from downloaded court documents.

Reads .txt files only. Run scripts/convert_pdfs.py first to extract text from PDFs.
"""

import re
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
TXT_DIR = DATA_DIR / "txt"
PDF_DIR = DATA_DIR / "pdfs"
DB_PATH = DATA_DIR / "verdicts.db"

COURTS = ["landsrettur", "heradsdomstolar", "haestirettur"]


def extract_case_number(filename: str) -> str:
    """Extract case number from filename.

    New format: 2018-0001.txt -> 1/2018, 2020-E-0102.txt -> E-102/2020
    Legacy:     1_2018.txt -> 1/2018, E-102_2020.txt -> E-102/2020
    """
    name = filename.rsplit(".", 1)[0]

    # New chronological format: YEAR-PREFIX-NUM
    m = re.match(r"(\d{4})-([A-Z])-(\d+)$", name)
    if m:
        year, prefix, num = m.group(1), m.group(2), m.group(3)
        return f"{prefix}-{num.lstrip('0') or '0'}/{year}"

    # New chronological format: YEAR-NUM
    m = re.match(r"(\d{4})-(\d+)$", name)
    if m:
        year, num = m.group(1), m.group(2)
        return f"{num.lstrip('0') or '0'}/{year}"

    # Legacy: PREFIX-NUM_YEAR
    m = re.match(r"([A-Z])-(\d+)_(\d{4})$", name)
    if m:
        return f"{m.group(1)}-{m.group(2)}/{m.group(3)}"

    # Legacy: NUM_YEAR or NUM___YEAR
    m = re.match(r"(\d+)_+(\d{4})$", name)
    if m:
        return f"{m.group(1)}/{m.group(2)}"

    return name


def init_db(conn: sqlite3.Connection):
    """Initialize database with FTS5 table."""
    conn.execute("DROP TABLE IF EXISTS verdicts")
    conn.execute("DROP TABLE IF EXISTS verdicts_fts")

    conn.execute("""
        CREATE TABLE verdicts (
            id INTEGER PRIMARY KEY,
            court TEXT NOT NULL,
            case_number TEXT NOT NULL,
            filename TEXT NOT NULL,
            text_length INTEGER,
            verdict_url TEXT,
            superseded_by INTEGER REFERENCES verdicts(id),
            UNIQUE(court, filename)
        )
    """)

    conn.execute("""
        CREATE VIRTUAL TABLE verdicts_fts USING fts5(
            case_number,
            content,
            content_rowid='id'
        )
    """)

    conn.commit()


def index_court(conn: sqlite3.Connection, court: str):
    """Index all .txt documents from a court."""
    txt_court_dir = TXT_DIR / court
    pdf_court_dir = PDF_DIR / court
    if not txt_court_dir.exists():
        print(f"  Directory not found: {txt_court_dir}")
        return 0

    txt_files = sorted(txt_court_dir.glob("*.txt"))
    if pdf_court_dir.exists():
        pdf_without_txt = [
            p for p in pdf_court_dir.glob("*.pdf")
            if not (txt_court_dir / p.with_suffix(".txt").name).exists()
        ]
        if pdf_without_txt:
            print(f"  WARNING: {len(pdf_without_txt)} PDFs without .txt — run scripts/convert_pdfs.py first")

    print(f"  Found {len(txt_files)} .txt files")

    indexed = 0
    for i, txt_path in enumerate(txt_files):
        if (i + 1) % 500 == 0:
            print(f"  Processing {i + 1}/{len(txt_files)}...")
            conn.commit()

        text = txt_path.read_text(encoding="utf-8")
        if not text or len(text) < 50:
            continue

        case_number = extract_case_number(txt_path.name)

        cursor = conn.execute("""
            INSERT OR IGNORE INTO verdicts (court, case_number, filename, text_length)
            VALUES (?, ?, ?, ?)
        """, (court, case_number, txt_path.name, len(text)))

        if cursor.rowcount > 0:
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

    conn.execute("CREATE INDEX IF NOT EXISTS idx_court ON verdicts(court)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_case_number ON verdicts(case_number)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_verdicts_superseded ON verdicts(superseded_by)")
    conn.commit()

    print(f"\nTotal indexed: {total} documents")
    print(f"Database size: {DB_PATH.stat().st_size / 1024 / 1024:.1f} MB")

    conn.close()


if __name__ == "__main__":
    main()

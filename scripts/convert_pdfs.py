#!/usr/bin/env python3
"""Convert all PDF verdicts to .txt files using pdfplumber.

Saves .txt alongside each .pdf. Skips if .txt already exists.
Run this before build_index.py.
"""

from pathlib import Path

import pdfplumber

DATA_DIR = Path(__file__).parent.parent / "data"
PDF_DIR = DATA_DIR / "pdfs"
TXT_DIR = DATA_DIR / "txt"
COURTS = ["landsrettur", "heradsdomstolar"]


def extract_text(pdf_path: Path) -> str:
    """Extract text from PDF file."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            parts = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    parts.append(text)
            return " ".join(parts)
    except Exception as e:
        print(f"  ERROR: {pdf_path.name}: {e}")
        return ""


def main():
    for court in COURTS:
        pdf_court_dir = PDF_DIR / court
        txt_court_dir = TXT_DIR / court
        txt_court_dir.mkdir(parents=True, exist_ok=True)

        if not pdf_court_dir.exists():
            print(f"Skipping {court} (PDF directory not found)")
            continue

        pdfs = sorted(pdf_court_dir.glob("*.pdf"))
        need_conversion = [
            p for p in pdfs
            if not (txt_court_dir / p.with_suffix(".txt").name).exists()
        ]

        print(f"\n{court}: {len(pdfs)} PDFs, {len(need_conversion)} need conversion")

        converted = 0
        failed = 0
        total = len(need_conversion)
        for i, pdf_path in enumerate(need_conversion):
            pct = (i + 1) * 100 // total if total else 100
            if (i + 1) % 50 == 0 or i == 0:
                print(f"  {i + 1}/{total} ({pct}%) — {pdf_path.name}", flush=True)

            text = extract_text(pdf_path)
            if text and len(text) >= 50:
                txt_path = txt_court_dir / pdf_path.with_suffix(".txt").name
                txt_path.write_text(text, encoding="utf-8")
                converted += 1
            else:
                failed += 1

        print(f"  Converted: {converted}, Failed: {failed}")

    print("\nDone. You can now run build_index.py.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""One-time migration: rename verdict files to chronological sort order.

Before: 1_2018.pdf, E-102_2020.pdf, 1___1999.txt
After:  2018-0001.pdf, 2020-E-0102.pdf, 1999-0001.txt

Also renames matching .txt files alongside PDFs.
Safe to run multiple times (skips already-renamed files).
"""

import re
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
PDF_DIR = DATA_DIR / "pdfs"
TXT_DIR = DATA_DIR / "txt"
DB_PATH = DATA_DIR / "verdicts.db"

COURTS = ["landsrettur", "heradsdomstolar", "haestirettur"]


def parse_old_name(name: str) -> tuple[str, str, str | None] | None:
    """Parse old filename (without extension) into (year, num_padded, prefix).

    Returns None if already in new format or unparseable.
    """
    # Skip if already in new format (starts with 4-digit year + dash)
    if re.match(r"\d{4}-", name):
        return None

    # PREFIX-NUM_YEAR (heradsdomstolar)
    m = re.match(r"([A-Z])-(\d+)_(\d{4})$", name)
    if m:
        return m.group(3), m.group(2).zfill(4), m.group(1)

    # NUM_YEAR or NUM___YEAR (landsrettur, haestirettur)
    m = re.match(r"(\d+)_+(\d{4})$", name)
    if m:
        return m.group(2), m.group(1).zfill(4), None

    return None


def new_name(year: str, num: str, prefix: str | None) -> str:
    """Build new filename stem."""
    if prefix:
        return f"{year}-{prefix}-{num}"
    return f"{year}-{num}"


def main():
    total_renamed = 0
    total_skipped = 0
    rename_map = {}  # old_filename -> new_filename for DB update

    for court in COURTS:
        renamed = 0
        skipped = 0

        # Rename in both pdf and txt directories
        for base_dir in [PDF_DIR, TXT_DIR]:
            court_dir = base_dir / court
            if not court_dir.exists():
                continue

            # Collect all files, group by stem
            stems = {}
            for f in court_dir.iterdir():
                if f.suffix in (".pdf", ".txt"):
                    stems.setdefault(f.stem, []).append(f)

            for stem, files in sorted(stems.items()):
                parsed = parse_old_name(stem)
                if parsed is None:
                    skipped += 1
                    continue

                year, num, prefix = parsed
                new_stem = new_name(year, num, prefix)

                for f in files:
                    new_path = f.with_name(f"{new_stem}{f.suffix}")
                    if new_path.exists() and new_path != f:
                        print(f"  CONFLICT: {f.name} -> {new_path.name} (target exists)")
                        continue
                    f.rename(new_path)
                    rename_map[f.name] = new_path.name
                    renamed += 1

        print(f"{court}: renamed {renamed} files, skipped {skipped}")
        total_renamed += renamed
        total_skipped += skipped

    print(f"\nTotal: {total_renamed} renamed, {total_skipped} skipped")

    # Update DB filenames
    if rename_map and DB_PATH.exists():
        print(f"\nUpdating {len(rename_map)} filenames in database...")
        try:
            conn = sqlite3.connect(DB_PATH)
            updated = 0
            for old, new in rename_map.items():
                cursor = conn.execute(
                    "UPDATE verdicts SET filename = ? WHERE filename = ?",
                    (new, old),
                )
                updated += cursor.rowcount
            conn.commit()
            conn.close()
            print(f"Updated {updated} DB rows")
        except sqlite3.OperationalError as e:
            print(f"DB update failed (locked?): {e}")
            print("Run this script again after build_index.py finishes.")


if __name__ == "__main__":
    main()

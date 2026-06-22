#!/usr/bin/env python3
"""Remove redundant keywords from adult-keywords.txt.

A keyword B is redundant when another keyword A (shorter or equal length) is a
literal substring of B — meaning any text that matches B will also match A, so
B adds nothing.

Usage:
    # Preview what would be removed
    python optimize_keywords.py --dry-run

    # Apply and overwrite the file
    python optimize_keywords.py

    # Specify a different file
    python optimize_keywords.py --dry-run path/to/keywords.txt
"""

import argparse
import sys
from pathlib import Path


def load_keywords(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line.strip()]


def find_redundant(keywords: list[str]) -> list[tuple[str, str]]:
    """Return list of (redundant_keyword, covering_keyword) pairs."""
    lower = [k.lower() for k in keywords]
    redundant: list[tuple[str, str]] = []
    redundant_set: set[int] = set()

    for i, b in enumerate(lower):
        if i in redundant_set:
            continue
        for j, a in enumerate(lower):
            if i == j or j in redundant_set:
                continue
            if a != b and a in b:
                redundant.append((keywords[i], keywords[j]))
                redundant_set.add(i)
                break

    return redundant


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "keywords_file",
        nargs="?",
        default=str(Path(__file__).parent.parent / "resources" / "adult-keywords.txt"),
        help="Path to keywords file (default: ../resources/adult-keywords.txt)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print changes without modifying the file")
    args = parser.parse_args()

    path = Path(args.keywords_file)
    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        sys.exit(1)

    keywords = load_keywords(path)
    print(f"Original keyword count: {len(keywords)}")

    pairs = find_redundant(keywords)
    redundant_kws = {b for b, _ in pairs}
    kept = [k for k in keywords if k not in redundant_kws]

    print(f"Redundant (covered by a shorter keyword): {len(pairs)}")
    print(f"Optimized keyword count: {len(kept)}")

    if pairs:
        print("\nRemoved keywords:")
        for redundant, covers in sorted(pairs, key=lambda x: x[0]):
            print(f"  - {redundant!r:40s}  covered by → {covers!r}")

    if not args.dry_run:
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        print(f"\nWrote {len(kept)} keywords to {path}")
    else:
        print("\n(dry-run — file not modified)")


if __name__ == "__main__":
    main()

"""Generate watermarked SYNTHETIC certificates (PNG + PDF + ground-truth JSON) for OCR testing.

Usage (from the repository root):
    python scripts/generate_synthetic_documents.py [--out data/raw/sample_documents] [--per-type 1]
        [--seed 2025] [--variants en,bilingual]

Every output is fictional and labelled "SYNTHETIC SAMPLE"; see ai_modules/ocr/synthetic.py.
Bilingual variants need a Devanagari font (Nirmala UI on Windows, Noto Sans Devanagari on
Linux, or ACA_DEVANAGARI_FONT); without one only English variants are written.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_modules.ocr.synthetic import VARIANTS, devanagari_font, generate  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "raw" / "sample_documents")
    parser.add_argument("--per-type", type=int, default=1, help="documents per type and variant")
    parser.add_argument("--seed", type=int, default=2025)
    parser.add_argument("--variants", default=",".join(VARIANTS), help="comma-separated: en,bilingual")
    args = parser.parse_args()

    variants = tuple(v.strip() for v in args.variants.split(",") if v.strip())
    unknown = set(variants) - set(VARIANTS)
    if unknown:
        parser.error(f"unknown variants: {', '.join(sorted(unknown))}")
    if "bilingual" in variants and devanagari_font() is None:
        print(
            "No Devanagari font found: skipping bilingual variants (set ACA_DEVANAGARI_FONT)", file=sys.stderr
        )

    docs = generate(args.out, seed=args.seed, per_type=args.per_type, variants=variants)
    for doc in docs:
        print(f"{doc.doc_id}: {doc.document_type} [{doc.variant}] -> {doc.expected_fields}")
    print(f"Wrote {len(docs)} synthetic documents (PNG + PDF + JSON) to {args.out}")


if __name__ == "__main__":
    main()

"""Build the RAG vector index from the official documents in data/raw/circulars/manifest.yaml.

    python scripts/build_vector_index.py               # download missing docs, extract, chunk, embed, save
    python scripts/build_vector_index.py --offline     # only use already-downloaded files
    python scripts/build_vector_index.py --pin-hashes  # write sha256 of unpinned docs into the manifest
    python scripts/build_vector_index.py --refresh     # re-download every document
    python scripts/build_vector_index.py --force       # re-embed even if the index is up to date

Idempotent: if the chunk ids (content hashes) and embedding model match the saved index, nothing
is re-embedded. A document whose sha256 no longer matches its pinned value stops the build.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_modules.rag.chunker import chunk_document  # noqa: E402
from ai_modules.rag.embedder import get_embedder  # noqa: E402
from ai_modules.rag.ingest import (  # noqa: E402
    MANIFEST_PATH,
    ChecksumMismatchError,
    ExtractedDocument,
    download,
    extract_document,
    load_manifest,
    update_manifest_hashes,
)
from ai_modules.rag.vector_store import (  # noqa: E402
    CHUNKS_FILE,
    DEFAULT_INDEX_DIR,
    INDEX_FILE,
    IndexModelMismatchError,
    IndexNotFoundError,
    VectorStore,
)


def _existing_chunk_ids(index_dir: Path, model_name: str) -> list[str] | None:
    try:
        store = VectorStore.load(index_dir, expected_model=model_name)
    except (IndexNotFoundError, IndexModelMismatchError):
        return None
    return [c.chunk_id for c in store.chunks]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    parser.add_argument("--offline", action="store_true", help="do not download; use files already on disk")
    parser.add_argument("--refresh", action="store_true", help="re-download documents that already exist")
    parser.add_argument("--pin-hashes", action="store_true", help="pin sha256 of unpinned documents")
    parser.add_argument("--force", action="store_true", help="rebuild even if the index is up to date")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    started = time.perf_counter()
    documents = load_manifest(args.manifest)
    print(f"Manifest: {len(documents)} official documents ({args.manifest})")

    extracted: list[ExtractedDocument] = []
    new_hashes: dict[str, str] = {}
    failures = 0
    for doc in documents:
        try:
            if not args.offline and (args.refresh or not doc.local_path.exists()):
                download(doc)
            if not doc.local_path.exists():
                print(f"  SKIP  {doc.doc_id}: not downloaded (offline mode)")
                failures += 1
                continue
            result = extract_document(doc)
        except ChecksumMismatchError as exc:
            print(f"  FAIL  {exc}")
            return 2
        except Exception as exc:  # network / parsing errors are reported per document
            print(f"  FAIL  {doc.doc_id}: {type(exc).__name__}: {exc}")
            failures += 1
            continue
        if doc.sha256 is None:
            new_hashes[doc.doc_id] = result.sha256
        if not result.pages:
            print(f"  SKIP  {doc.doc_id}: no extractable text (scanned or legacy-font document)")
            continue
        extracted.append(result)
        print(f"  OK    {doc.doc_id}: {len(result.pages)} text pages, {result.char_count:,} chars")

    if new_hashes:
        if args.pin_hashes:
            changed = update_manifest_hashes(new_hashes, args.manifest)
            print(f"Pinned sha256 for {changed} document(s) in {args.manifest}")
        else:
            print(f"WARNING: {len(new_hashes)} document(s) have no pinned sha256; run with --pin-hashes")

    chunks = [chunk for document in extracted for chunk in chunk_document(document)]
    per_doc: dict[str, int] = {}
    for chunk in chunks:
        per_doc[chunk.doc_id] = per_doc.get(chunk.doc_id, 0) + 1
    print(f"Chunks: {len(chunks)} from {len(extracted)} documents")
    for doc_id, count in per_doc.items():
        print(f"  {doc_id}: {count}")

    embedder = get_embedder()
    existing = _existing_chunk_ids(args.index_dir, embedder.model_name)
    if not args.force and existing == [c.chunk_id for c in chunks]:
        print(f"Index in {args.index_dir} is up to date ({len(chunks)} chunks, {embedder.model_name})")
        return 1 if failures else 0

    embed_start = time.perf_counter()
    vectors = embedder.embed([c.text for c in chunks])
    embed_seconds = time.perf_counter() - embed_start
    store = VectorStore.build(
        chunks,
        vectors,
        model_name=embedder.model_name,
        documents={d.source.doc_id: d.sha256 for d in extracted},
    )
    store.save(args.index_dir)
    size = sum((args.index_dir / name).stat().st_size for name in (INDEX_FILE, CHUNKS_FILE))
    print(
        f"Index saved to {args.index_dir}: {len(store)} vectors x {store.manifest.dimension} dims "
        f"({embedder.model_name}, device={embedder.device}), {size / 1024:.0f} KiB on disk; "
        f"embedding took {embed_seconds:.1f}s, total {time.perf_counter() - started:.1f}s"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

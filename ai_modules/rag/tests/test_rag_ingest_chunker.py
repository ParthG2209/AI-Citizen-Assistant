from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ai_modules.rag.chunker import chunk_document, stable_chunk_id
from ai_modules.rag.ingest import (
    MANIFEST_PATH,
    ExtractedDocument,
    ManifestError,
    PageText,
    clean_pages,
    html_to_text,
    is_garbled,
    is_official_url,
    load_manifest,
    update_manifest_hashes,
)

# ------------------------------------------------------------------ ingest


def test_official_url_allowlist():
    assert is_official_url("https://pmkisan.gov.in/Documents/x.pdf")
    assert is_official_url("https://khargone.nic.in/en/scheme/x")
    assert is_official_url("https://sansad.in/getFile/x.pdf")
    assert not is_official_url("http://pmkisan.gov.in/x.pdf")  # plain http
    assert not is_official_url("https://pmkisan.gov.in.evil.com/x.pdf")
    assert not is_official_url("https://www.govtschemes.in/pm-kisan")


def test_manifest_rejects_non_official_source(tmp_path: Path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "documents:\n"
        "  - doc_id: blog_post\n"
        "    title: Some blog\n"
        "    url: https://example.com/pm-kisan\n"
        "    publisher: Blog\n"
        "    scheme_ids: [pm_kisan]\n"
        "    language: en\n"
        "    format: html\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="official"):
        load_manifest(manifest)


def test_committed_manifest_is_valid_and_pinned():
    docs = load_manifest(MANIFEST_PATH)
    assert len(docs) >= 10
    assert all(d.sha256 and len(d.sha256) == 64 for d in docs), (
        "every indexed document must have a pinned sha256"
    )


def test_manifest_scheme_ids_match_rule_files():
    from ai_modules.rule_engine import RuleEngine

    rule_ids = {rule.scheme_id for rule in RuleEngine().schemes()}
    manifest_ids = {sid for d in load_manifest(MANIFEST_PATH) for sid in d.scheme_ids}
    assert manifest_ids <= rule_ids, f"manifest references unknown schemes: {manifest_ids - rule_ids}"
    assert rule_ids <= manifest_ids, f"schemes without any evidence document: {rule_ids - manifest_ids}"


def test_update_manifest_hashes_keeps_comments(tmp_path: Path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "# comment stays\ndocuments:\n  - doc_id: a_doc\n    title: A\n    sha256:\n"
        "  - doc_id: b_doc\n    title: B\n    sha256: abc\n",
        encoding="utf-8",
    )
    assert update_manifest_hashes({"a_doc": "f" * 64}, manifest) == 1
    text = manifest.read_text(encoding="utf-8")
    assert "# comment stays" in text
    assert f"sha256: {'f' * 64}" in text
    assert "sha256: abc" in text


def test_html_to_text_drops_scripts_navigation_and_trims_markers():
    markup = """<html><head><title>t</title><script>var x = 1;</script></head><body>
    <nav><a>Home</a><a>Login</a></nav>
    <form><div>Scheme Name:</div><span>Gaon Ki Beti</span>
    <div>पात्रता</div><p>बालिका ने १२वीं की परीक्षा में ६०% अंक प्राप्त किये हों</p>
    <div>How to Apply/Get Benefit</div><p>footer text</p></form>
    <footer>Designed by NIC</footer></body></html>"""
    text = html_to_text(markup, start="Scheme Name:", end="How to Apply/Get Benefit")
    assert text.splitlines() == [
        "Scheme Name:",
        "Gaon Ki Beti",
        "पात्रता",
        "बालिका ने १२वीं की परीक्षा में ६०% अंक प्राप्त किये हों",
    ]
    with pytest.raises(ManifestError, match="marker"):
        html_to_text(markup, start="not on the page")


def test_clean_pages_removes_running_headers_page_numbers_and_hyphenation():
    pages = [
        PageText(
            page=n,
            text=f"Operational Guidelines PM-KISAN\nBody text of page {n} about land-\n"
            f"holding farmers.\nPage {n} of 5",
        )
        for n in range(1, 6)
    ]
    cleaned = clean_pages(pages)
    assert [p.page for p in cleaned] == [1, 2, 3, 4, 5]
    for page in cleaned:
        assert "Operational Guidelines PM-KISAN" not in page.text
        assert "of 5" not in page.text
        assert "landholding farmers." in page.text


def test_clean_pages_drops_table_of_contents_pages():
    contents = "\n".join(f"{n}. Chapter heading number {n} {n * 3}-{n * 3 + 2}" for n in range(1, 11))
    bodies = ["Scheme objective text.", "Definition of family.", "Exclusion criteria.", "Benefit transfer."]
    pages = [PageText(1, contents)] + [PageText(n, body) for n, body in enumerate(bodies, start=2)]
    assert [p.page for p in clean_pages(pages)] == [2, 3, 4, 5]


def test_legacy_font_pages_are_detected_as_garbled():
    assert is_garbled("Ĥæन 06: Èया इस योजना का लाभ èवाÚयायी छाğɉ को भी ͧ मल सकता")
    assert not is_garbled("बालिका ने १२वीं की परीक्षा में ६०% या उससे ज्यादा अंक प्राप्त किये हों")
    assert not is_garbled(
        "The benefit shall be paid to only those farmers’ families whose names are entered."
    )


# ------------------------------------------------------------------ chunker


def test_chunker_detects_sections_and_paragraphs(fixture_chunks):
    pm_kisan = [c for c in fixture_chunks if c.doc_id == "pm_kisan_guidelines"]
    sections = [c.section for c in pm_kisan]
    assert "2. Objective and Benefits" in sections
    assert "3. Definition of farmer’s family" in sections
    exclusion = [c for c in pm_kisan if c.section and c.section.startswith("4 Exclusions")]
    assert exclusion, sections
    assert any("Institutional Land holders" in c.text for c in exclusion)
    # Wrapped continuation lines ("categories :", "eligible for benefit ...") must not become headings.
    assert not any(s and s.lower().startswith(("categories", "eligible")) for s in sections)


def test_chunk_metadata_is_complete(fixture_chunks, fixture_documents):
    by_doc = {d.source.doc_id: d.source for d in fixture_documents}
    for chunk in fixture_chunks:
        src = by_doc[chunk.doc_id]
        assert chunk.url == src.url
        assert chunk.scheme_ids == src.scheme_ids
        assert chunk.source_title == src.title
        assert chunk.language == src.language
        assert chunk.page in {
            p.page for d in fixture_documents if d.source.doc_id == chunk.doc_id for p in d.pages
        }
        assert len(chunk.chunk_id) == 20
        assert len(chunk.text) <= 400
    assert len({c.chunk_id for c in fixture_chunks}) == len(fixture_chunks)


def test_chunk_ids_are_stable_and_content_addressed(fixture_documents):
    document = fixture_documents[0]
    first = chunk_document(document, chunk_size=400, chunk_overlap=40)
    second = chunk_document(document, chunk_size=400, chunk_overlap=40)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]

    edited_text = document.pages[0].text.replace("Rs.6000/-", "Rs.6001/-")
    edited = ExtractedDocument(
        document.source, document.sha256, [replace(document.pages[0], text=edited_text)]
    )
    changed = {c.chunk_id for c in chunk_document(edited, chunk_size=400, chunk_overlap=40)}
    assert changed != {c.chunk_id for c in first}
    assert stable_chunk_id("d", 1, "s", "text") == stable_chunk_id("d", 1, "s", "text")
    assert stable_chunk_id("d", 1, "s", "text") != stable_chunk_id("d", 2, "s", "text")


def test_chunk_round_trips_through_dict(fixture_chunks):
    from ai_modules.rag.chunker import Chunk

    for chunk in fixture_chunks:
        assert Chunk.from_dict(chunk.to_dict()) == chunk

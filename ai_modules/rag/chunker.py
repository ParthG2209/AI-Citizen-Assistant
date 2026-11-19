"""Section-aware chunking of cleaned official documents.

Lines that look like headings (numbered headings, "Chapter"/"Annexure"/"अध्याय" lines, short
upper-case titles, HTML portal labels ending in ":") start a new section; numbered paragraphs
("4.2 The following ...") start a new segment whose paragraph number is kept. Consecutive
segments of the same section are merged up to `chunk_size` characters; longer segments are split
with LangChain's `RecursiveCharacterTextSplitter`. Chunks never cross page boundaries, so every
chunk has an exact page number for citations.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any

from .ingest import ExtractedDocument

DEFAULT_CHUNK_SIZE = 900
DEFAULT_CHUNK_OVERLAP = 120
MIN_CHUNK_CHARS = 40

# "4.", "4.2", "4.2.1", "(4)", "4)" followed by text
_NUMBERED_RE = re.compile(r"^(?:\(?(\d{1,2}(?:\.\d{1,2}){0,3})[.)]|(\d{1,2}(?:\.\d{1,2}){1,3}))\s+(\S.*)$")
_KEYWORD_HEADING_RE = re.compile(
    r"^(?:chapter|annexure|annex|appendix|schedule|part|अध्याय|परिशिष्ट|भाग)\s*[-–:.]?\s*"
    r"(?:[0-9०-९]+|[IVXLC]+|[A-Z])(?:\W|$)",
    re.IGNORECASE,
)
_SENTENCE_END_RE = re.compile(r"[.;,]$")
_BLOCK_END_RE = re.compile(r"[.:;?!।)\"”'’]$")
_BARE_NUMBER_HEADING_RE = re.compile(r"^(\d{1,2})\s+([A-Z][^.;,:]{2,60})$")  # "4 Exclusions"


@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_id: str
    doc_id: str
    text: str
    scheme_ids: tuple[str, ...]
    source_title: str
    url: str
    publisher: str
    page: int | None
    section: str | None
    language: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scheme_ids"] = list(self.scheme_ids)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Chunk:
        return cls(**{**data, "scheme_ids": tuple(data["scheme_ids"])})


def stable_chunk_id(doc_id: str, page: int | None, section: str | None, text: str) -> str:
    """Content-addressed id: identical input documents always produce identical chunk ids."""
    payload = "\x1f".join([doc_id, str(page), section or "", text])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _is_heading(line: str, after_break: bool) -> bool:
    """`after_break`: the previous line ended a sentence/block, so `line` cannot be a wrapped continuation."""
    words = line.split()
    if not words or len(line) > 90 or len(words) > 12:
        return False
    if _KEYWORD_HEADING_RE.match(line):
        return True
    if not after_break:
        return False
    numbered = _NUMBERED_RE.match(line)
    if numbered:
        # "3. Exclusion Criteria" is a heading; "3.1 The landholding ... shall be" is a paragraph.
        body = numbered.group(3)
        return not _SENTENCE_END_RE.search(body) and len(body.split()) <= 8 and body[:1].isupper()
    if _BARE_NUMBER_HEADING_RE.match(line) and len(words) <= 6:
        return True
    letters = [c for c in line if c.isalpha()]
    if (
        len(letters) >= 4
        and all(c.isupper() for c in letters if c.isascii())
        and any(c.isascii() for c in letters)
    ):
        return not _SENTENCE_END_RE.search(line)
    return line.endswith(":") and len(words) <= 6 and not line[:1].islower()


def _numbered_para(line: str) -> str | None:
    match = _NUMBERED_RE.match(line)
    return (match.group(1) or match.group(2)) if match else None


def _segments(
    lines: list[str], heading: str | None
) -> tuple[list[tuple[str | None, str | None, str]], str | None]:
    """Group a page's lines into (heading, paragraph-number, text) segments. Returns last heading too."""
    segments: list[tuple[str | None, str | None, str]] = []
    para: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            segments.append((heading, para, " ".join(buffer)))
            buffer.clear()

    after_break = True
    for line in lines:
        if _is_heading(line, after_break):
            flush()
            heading = line.rstrip(":").strip()
            bare = _BARE_NUMBER_HEADING_RE.match(line)
            para = _numbered_para(line) or (bare.group(1) if bare else None)
            after_break = True
            continue
        number = _numbered_para(line) if after_break else None
        if number:
            flush()
            para = number
        buffer.append(line)
        after_break = bool(_BLOCK_END_RE.search(line))
    flush()
    return segments, heading


def _section_label(heading: str | None, para: str | None) -> str | None:
    if heading and para and not heading.startswith(para):
        return f"{heading} (para {para})"
    if heading:
        return heading
    return f"para {para}" if para else None


def chunk_document(
    document: ExtractedDocument,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Chunk]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "। ", ". ", "; ", ", ", " ", ""],
    )
    source = document.source
    chunks: list[Chunk] = []
    seen: set[str] = set()
    heading: str | None = None

    def emit(page: int, label: str | None, text: str) -> None:
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < MIN_CHUNK_CHARS:
            return
        chunk_id = stable_chunk_id(source.doc_id, page, label, text)
        if chunk_id in seen:
            return
        seen.add(chunk_id)
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                doc_id=source.doc_id,
                text=text,
                scheme_ids=source.scheme_ids,
                source_title=source.title,
                url=source.url,
                publisher=source.publisher,
                page=page,
                section=label,
                language=source.language,
            )
        )

    for page in document.pages:
        lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        segments, heading = _segments(lines, heading)
        # Merge consecutive segments of the same heading up to chunk_size.
        current_heading: str | None = None
        current_para: str | None = None
        current: list[str] = []
        for seg_heading, para, text in segments:
            same_section = current and seg_heading == current_heading
            if same_section and sum(len(t) for t in current) + len(text) + 1 <= chunk_size:
                current.append(text)
                continue
            if current:
                for piece in splitter.split_text("\n".join(current)):
                    emit(page.page, _section_label(current_heading, current_para), piece)
            current_heading, current_para, current = seg_heading, para, [text]
        if current:
            for piece in splitter.split_text("\n".join(current)):
                emit(page.page, _section_label(current_heading, current_para), piece)
    return chunks


def chunk_documents(documents: list[ExtractedDocument], **kwargs: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    for document in documents:
        chunks.extend(chunk_document(document, **kwargs))
    return chunks

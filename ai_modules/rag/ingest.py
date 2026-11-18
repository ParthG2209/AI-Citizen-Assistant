"""Download, verify and extract official scheme documents listed in the circulars manifest.

The manifest (`data/raw/circulars/manifest.yaml`) is the single list of evidence documents. Every
entry points at an official government host (*.gov.in / *.nic.in, plus the allow-listed official
portals in `OFFICIAL_HOSTS`). Downloaded files are kept
locally (gitignored) and checked against the pinned sha256:

* PDF  -> sha256 of the downloaded file bytes.
* HTML -> sha256 of the extracted, trimmed page text (raw markup of these portals carries
  per-request tokens such as ASP.NET view state, so the bytes change on every request while
  the published content does not).

Text is extracted per page (PyMuPDF for PDF, a small stdlib HTML-to-text for portal pages),
then cleaned: repeated headers/footers and page-number lines removed, hyphenated line breaks
joined and whitespace normalised. Page numbers are kept (1-based; HTML pages are page 1).
"""

from __future__ import annotations

import hashlib
import re
import ssl
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CIRCULARS_DIR = REPO_ROOT / "data" / "raw" / "circulars"
MANIFEST_PATH = CIRCULARS_DIR / "manifest.yaml"

OFFICIAL_DOMAIN_SUFFIXES = (".gov.in", ".nic.in")
# Official portals outside *.gov.in / *.nic.in: Parliament of India, MP School Education Portal 3.0.
OFFICIAL_HOSTS = frozenset({"sansad.in", "educationportal3.in"})
SUPPORTED_FORMATS = ("pdf", "html")
SUPPORTED_LANGUAGES = ("en", "hi")
USER_AGENT = "AI-Citizen-Assistant/1.0 (EPICS research project; official-document indexer)"


class ManifestError(ValueError):
    """The manifest is malformed or lists a non-official source."""


class ChecksumMismatchError(RuntimeError):
    """A downloaded or cached document does not match the sha256 pinned in the manifest."""

    def __init__(self, doc_id: str, expected: str, actual: str) -> None:
        super().__init__(
            f"{doc_id}: sha256 mismatch (manifest {expected[:12]}…, got {actual[:12]}…). "
            "The official document changed or the download is corrupt; review it and re-pin the hash."
        )
        self.doc_id = doc_id
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True, slots=True)
class SourceDocument:
    doc_id: str
    title: str
    url: str
    publisher: str
    scheme_ids: tuple[str, ...]
    language: str
    format: str  # "pdf" | "html"
    sha256: str | None = None
    # Some MP NIC portals only speak TLS with legacy renegotiation, which OpenSSL 3 refuses by default.
    # Certificate verification stays on; the content is still checked against the pinned sha256.
    legacy_tls: bool = False
    content_start: str | None = None  # HTML: keep text from the first occurrence of this marker
    content_end: str | None = None  # HTML: drop text from the first occurrence of this marker
    notes: str | None = None

    @property
    def local_path(self) -> Path:
        return CIRCULARS_DIR / f"{self.doc_id}.{self.format}"


@dataclass(frozen=True, slots=True)
class PageText:
    page: int  # 1-based
    text: str


@dataclass(slots=True)
class ExtractedDocument:
    source: SourceDocument
    sha256: str
    pages: list[PageText] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return sum(len(p.text) for p in self.pages)


def is_official_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    host = host.removeprefix("www.")
    return parsed.scheme == "https" and (host.endswith(OFFICIAL_DOMAIN_SUFFIXES) or host in OFFICIAL_HOSTS)


def _parse_entry(raw: dict[str, Any]) -> SourceDocument:
    try:
        doc = SourceDocument(
            doc_id=str(raw["doc_id"]),
            title=str(raw["title"]),
            url=str(raw["url"]),
            publisher=str(raw["publisher"]),
            scheme_ids=tuple(str(s) for s in raw["scheme_ids"]),
            language=str(raw["language"]),
            format=str(raw["format"]),
            sha256=str(raw["sha256"]) if raw.get("sha256") else None,
            legacy_tls=bool(raw.get("legacy_tls", False)),
            content_start=raw.get("content_start"),
            content_end=raw.get("content_end"),
            notes=raw.get("notes"),
        )
    except KeyError as exc:
        raise ManifestError(f"manifest entry {raw.get('doc_id', '?')} is missing {exc}") from exc
    if not re.fullmatch(r"[a-z0-9_]+", doc.doc_id):
        raise ManifestError(f"{doc.doc_id}: doc_id must be snake_case")
    if not is_official_url(doc.url):
        raise ManifestError(f"{doc.doc_id}: {doc.url} is not an https URL on an official government host")
    if doc.format not in SUPPORTED_FORMATS:
        raise ManifestError(f"{doc.doc_id}: unsupported format {doc.format!r}")
    if doc.language not in SUPPORTED_LANGUAGES:
        raise ManifestError(f"{doc.doc_id}: unsupported language {doc.language!r}")
    if not doc.scheme_ids:
        raise ManifestError(f"{doc.doc_id}: scheme_ids must not be empty")
    return doc


def load_manifest(path: Path = MANIFEST_PATH) -> list[SourceDocument]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    docs = [_parse_entry(entry) for entry in data.get("documents", [])]
    ids = Counter(d.doc_id for d in docs)
    duplicates = [doc_id for doc_id, n in ids.items() if n > 1]
    if duplicates:
        raise ManifestError(f"duplicate doc_id(s): {duplicates}")
    return docs


def update_manifest_hashes(hashes: dict[str, str], path: Path = MANIFEST_PATH) -> int:
    """Pin sha256 values in the manifest in place (line-based, so comments survive). Returns #changed."""
    lines = path.read_text(encoding="utf-8").splitlines()
    current: str | None = None
    changed = 0
    for i, line in enumerate(lines):
        match = re.match(r"^\s*-\s*doc_id:\s*(\S+)", line)
        if match:
            current = match.group(1)
            continue
        hash_line = re.match(r"^(\s*)sha256:\s*(\S*)", line)
        if hash_line and current in hashes:
            new_line = f"{hash_line.group(1)}sha256: {hashes[current]}"
            if new_line != line:
                lines[i] = new_line
                changed += 1
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- download


def download(doc: SourceDocument, *, timeout: float = 90.0) -> Path:
    """Fetch `doc.url` into `doc.local_path` (overwrites). Content checks happen in `extract_document`."""
    import httpx
    import truststore

    # truststore uses the OS certificate store, which (unlike certifi) completes the incomplete
    # certificate chains some government hosts serve (e.g. pmkisan.gov.in).
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if doc.legacy_tls:
        context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)

    with httpx.Client(
        follow_redirects=True,
        timeout=timeout,
        verify=context,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        response = client.get(doc.url)
        response.raise_for_status()
        final_url = str(response.url)
        if not is_official_url(final_url):
            raise ManifestError(f"{doc.doc_id}: redirected to non-official URL {final_url}")
        body = response.content
    if doc.format == "pdf" and not body.startswith(b"%PDF"):
        raise ManifestError(f"{doc.doc_id}: expected a PDF but the server returned something else")
    doc.local_path.parent.mkdir(parents=True, exist_ok=True)
    doc.local_path.write_bytes(body)
    return doc.local_path


# ---------------------------------------------------------------- extraction

_SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "nav",
    "header",
    "footer",
    "select",
    "button",
    "svg",
    "iframe",
    "template",
    "head",
}
_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "ul",
    "ol",
    "tr",
    "td",
    "th",
    "table",
    "section",
    "article",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "main",
    "dt",
    "dd",
    "hr",
}


class _HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(markup: str, start: str | None = None, end: str | None = None) -> str:
    """Visible text of an HTML page, one block per line, optionally trimmed between two markers."""
    parser = _HtmlText()
    parser.feed(markup)
    parser.close()
    text = unescape("".join(parser.parts))
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    if start:
        idx = text.find(start)
        if idx < 0:
            raise ManifestError(f"content_start marker {start!r} not found; the page layout changed")
        text = text[idx:]
    if end:
        idx = text.find(end)
        if idx < 0:
            raise ManifestError(f"content_end marker {end!r} not found; the page layout changed")
        text = text[:idx]
    return text.strip()


def _pdf_pages(path: Path) -> list[PageText]:
    import pymupdf

    with pymupdf.open(path) as pdf:
        return [PageText(page=i + 1, text=page.get_text("text", sort=True)) for i, page in enumerate(pdf)]


# ---------------------------------------------------------------- cleaning

_PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:page\s*)?[-–(\[]?\s*\d{1,4}\s*[-–)\]]?\s*(?:(?:of|/)\s*\d{1,4})?\s*$", re.IGNORECASE
)
_HYPHEN_BREAK_RE = re.compile(r"([A-Za-z]{2,})-\n\s*([a-z]{2,})")
_INLINE_SPACE_RE = re.compile(r"[ \t\u00a0\u200b]+")
# Control characters and Latin-1/Latin-Extended letters are what legacy (non-Unicode) Hindi fonts
# such as Kruti Dev / Chanakya turn into when extracted; such pages are unreadable for retrieval.
_MOJIBAKE_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\u00c0-\u024f\u0370-\u03ff]")
MAX_MOJIBAKE_RATIO = 0.02


def is_garbled(text: str) -> bool:
    letters = sum(1 for c in text if not c.isspace())
    return letters > 0 and len(_MOJIBAKE_RE.findall(text)) / letters > MAX_MOJIBAKE_RATIO


def _signature(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip().lower())


def _margin_indices(line_count: int) -> set[int]:
    """Line positions treated as page margin: 2 lines top/bottom, 1 on short pages."""
    margin = 2 if line_count >= 6 else 1
    return set(range(min(margin, line_count))) | set(range(max(line_count - margin, 0), line_count))


def _repeated_margin_lines(pages: list[list[str]]) -> set[str]:
    """Signatures of lines that recur in the top/bottom margin of many pages (running headers/footers)."""
    if len(pages) < 4:
        return set()
    counts: Counter[str] = Counter()
    for lines in pages:
        counts.update({_signature(lines[i]) for i in _margin_indices(len(lines))})
    threshold = max(3, len(pages) // 2)
    return {sig for sig, n in counts.items() if n >= threshold and len(sig) < 120}


_TOC_LINE_RE = re.compile(
    r"(\.{4,}|…{2,})|(?:^|\s)[ivxl\d]{1,4}(?:\s*[-–]\s*[ivxl\d]{1,4})?\)?$", re.IGNORECASE
)


def _is_table_of_contents(lines: list[str]) -> bool:
    """Most lines end in a page reference ("3-5", "98-99", ".... 12"): a contents page or blank form."""
    return len(lines) >= 8 and sum(bool(_TOC_LINE_RE.search(line)) for line in lines) / len(lines) >= 0.5


def clean_pages(pages: Iterable[PageText]) -> list[PageText]:
    """Remove running headers/footers, page numbers and contents pages; join hyphenated breaks;
    normalise whitespace."""
    pages = list(pages)
    split = [[_INLINE_SPACE_RE.sub(" ", line).strip() for line in p.text.splitlines()] for p in pages]
    split = [[line for line in lines if line] for lines in split]
    repeated = _repeated_margin_lines(split)
    multi_page = len(pages) >= 4
    cleaned: list[PageText] = []
    for page, lines in zip(pages, split, strict=True):
        if multi_page and _is_table_of_contents(lines):
            continue
        kept: list[str] = []
        margin = _margin_indices(len(lines))
        for idx, line in enumerate(lines):
            in_margin = idx in margin
            if _PAGE_NUMBER_RE.match(line):
                continue
            if in_margin and _signature(line) in repeated:
                continue
            kept.append(line)
        text = _HYPHEN_BREAK_RE.sub(r"\1\2", "\n".join(kept)).strip()
        if text and not is_garbled(text):
            cleaned.append(PageText(page=page.page, text=text))
    return cleaned


def text_sha256(pages: Iterable[PageText]) -> str:
    return sha256_bytes("\n\f\n".join(p.text for p in pages).encode("utf-8"))


def extract_document(doc: SourceDocument, path: Path | None = None) -> ExtractedDocument:
    """Extract + clean a downloaded document and verify it against the manifest hash (if pinned)."""
    path = path or doc.local_path
    if not path.exists():
        raise FileNotFoundError(f"{doc.doc_id}: {path} not downloaded; run scripts/build_vector_index.py")
    if doc.format == "pdf":
        digest = sha256_bytes(path.read_bytes())
        if doc.sha256 and digest != doc.sha256:
            raise ChecksumMismatchError(doc.doc_id, doc.sha256, digest)
        pages = clean_pages(_pdf_pages(path))
    else:
        markup = path.read_bytes().decode("utf-8", errors="replace")
        pages = clean_pages([PageText(page=1, text=html_to_text(markup, doc.content_start, doc.content_end))])
        digest = text_sha256(pages)
        if doc.sha256 and digest != doc.sha256:
            raise ChecksumMismatchError(doc.doc_id, doc.sha256, digest)
    return ExtractedDocument(source=doc, sha256=digest, pages=pages)

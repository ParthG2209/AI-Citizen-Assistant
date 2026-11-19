"""Normalise raw OCR snippets (Hindi + English) into canonical profile values.

Every function returns ``None`` when the text cannot be mapped confidently; callers treat
``None`` as "field not found" rather than guessing. Enum outputs use the values declared in
``ai_modules/rule_engine/fields.py``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher

# Devanagari digits ०-९ -> ASCII 0-9.
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_NUKTA = "\u093c"


def normalize_text(text: str) -> str:
    """NFC-normalise, convert Devanagari digits and collapse whitespace."""
    text = unicodedata.normalize("NFC", text).translate(_DEVANAGARI_DIGITS)
    return re.sub(r"\s+", " ", text).strip()


def _is_letter(ch: str) -> bool:
    """Letters and combining marks (Devanagari matras/viramas are marks, not ``\\w``)."""
    return unicodedata.category(ch)[0] in "LM"


def match_key(text: str) -> str:
    """Aggressive key used for dictionary/fuzzy lookups (case, nukta, punctuation-insensitive)."""
    text = unicodedata.normalize("NFD", normalize_text(text)).replace(_NUKTA, "")
    text = unicodedata.normalize("NFC", text).casefold()
    return "".join(ch for ch in text if _is_letter(ch) or ch.isdigit())


# --------------------------------------------------------------------------- amounts
_LAKH_WORDS = r"(?:lakh|lakhs|lac|lacs|लाख)"
_CRORE_WORDS = r"(?:crore|crores|करोड़|करोड)"
_CURRENCY = r"(?:\brs\.?|\binr|₹|रु\.?|रू\.?|रुपये|रुपए|\brupees)"
_NUMBER = r"\d+(?:[.,]\d+)*"
_AMOUNT_RE = re.compile(
    rf"(?P<cur>{_CURRENCY})?\s*(?P<num>{_NUMBER})\s*(?P<unit>{_LAKH_WORDS}|{_CRORE_WORDS})?",
    re.IGNORECASE,
)


def _parse_number_token(token: str) -> float | None:
    """Parse ``1,20,000`` / ``120000.50`` / ``1.5``, tolerating OCR's '.'-for-',' confusion.

    A final ``.`` followed by 1-2 digits is a decimal point; otherwise separators followed by
    2-3 digit groups ending in a 3-digit group are digit grouping (``4.16,000`` -> 416000).
    Anything else (e.g. ``12.03.2025``) is not an amount.
    """
    groups = re.split(r"[.,]", token)
    seps = re.findall(r"[.,]", token)
    if not seps:
        return float(token)
    if seps[-1] == "." and len(groups[-1]) <= 2:
        integer = groups[:-1]
        if len(integer) > 1 and not all(len(g) in (2, 3) for g in integer[1:]):
            return None
        return float("".join(integer) + "." + groups[-1])
    if len(groups[-1]) == 3 and all(len(g) in (2, 3) for g in groups[1:]):
        return float("".join(groups))
    return None


def parse_amount_inr(text: str) -> float | None:
    """Parse an INR amount such as ``Rs. 1,20,000/-``, ``₹1.2 lakh`` or ``१२०००० रुपये``.

    When several numbers are present, an amount carrying a currency marker or a lakh/crore
    unit is preferred over a bare number (bare numbers may be dates or reference numbers).
    """
    text = normalize_text(text)
    candidates: list[tuple[int, float]] = []
    for m in _AMOUNT_RE.finditer(text):
        value = _parse_number_token(m.group("num"))
        if value is None:
            continue
        unit = (m.group("unit") or "").casefold()
        if unit and re.fullmatch(_LAKH_WORDS, unit, re.IGNORECASE):
            value *= 100_000
        elif unit and re.fullmatch(_CRORE_WORDS, unit, re.IGNORECASE):
            value *= 10_000_000
        # Currency word may also follow the number ("120000 रुपये").
        tail = text[m.end() : m.end() + 12]
        has_currency = bool(m.group("cur") or unit or re.match(rf"\s*/?-?\s*{_CURRENCY}", tail, re.I))
        candidates.append((2 if has_currency else 1, value))
    if not candidates:
        return None
    best_rank = max(rank for rank, _ in candidates)
    best = [value for rank, value in candidates if rank == best_rank]
    return best[0]


# --------------------------------------------------------------------------- percentages
_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d{1,2})?)\s*(?:%|percent|प्रतिशत)", re.IGNORECASE)
_MARKS_RE = re.compile(r"(\d{2,4}(?:\.\d+)?)\s*(?:/|out\s+of|में\s+से)\s*(\d{2,4})", re.IGNORECASE)


def parse_percentage(text: str) -> float | None:
    """Parse ``78.4%`` / ``78.40 percent`` or a bare number in a percentage context."""
    text = normalize_text(text)
    m = _PERCENT_RE.search(text)
    if m is None:
        m = re.search(r"(?<![\d.])(\d{1,3}(?:\.\d{1,2})?)(?![\d.])", text)
    if m is None:
        return None
    value = float(m.group(1))
    return value if 0 <= value <= 100 else None


def percentage_from_marks(obtained: float, maximum: float) -> float | None:
    if maximum <= 0 or obtained < 0 or obtained > maximum:
        return None
    return round(obtained * 100 / maximum, 2)


def parse_marks_ratio(text: str) -> tuple[float, float] | None:
    """Parse ``392 / 500`` or ``392 out of 500`` into (obtained, maximum)."""
    m = _MARKS_RE.search(normalize_text(text))
    if m is None:
        return None
    return float(m.group(1)), float(m.group(2))


# --------------------------------------------------------------------------- dates / years
_MONTHS = {
    "jan": 1, "january": 1, "जनवरी": 1,
    "feb": 2, "february": 2, "फरवरी": 2,
    "mar": 3, "march": 3, "मार्च": 3,
    "apr": 4, "april": 4, "अप्रैल": 4,
    "may": 5, "मई": 5,
    "jun": 6, "june": 6, "जून": 6,
    "jul": 7, "july": 7, "जुलाई": 7,
    "aug": 8, "august": 8, "अगस्त": 8,
    "sep": 9, "sept": 9, "september": 9, "सितंबर": 9, "सितम्बर": 9,
    "oct": 10, "october": 10, "अक्टूबर": 10,
    "nov": 11, "november": 11, "नवंबर": 11, "नवम्बर": 11,
    "dec": 12, "december": 12, "दिसंबर": 12, "दिसम्बर": 12,
}  # fmt: skip
_NUMERIC_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)")
_ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_WORD_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[-\s]?\s*([^\W\d_]+)\.?,?\s*[-\s]?\s*(\d{4})(?!\d)")
_YEAR_RE = re.compile(r"(?<!\d)(19[5-9]\d|20\d\d)(?!\d)")


def parse_date(text: str) -> date | None:
    """Parse Indian-style dates: ``dd/mm/yyyy``, ``dd-mm-yyyy``, ``yyyy-mm-dd``, ``12 May 2005``."""
    text = normalize_text(text)
    for regex, order in ((_ISO_DATE_RE, "ymd"), (_NUMERIC_DATE_RE, "dmy")):
        m = regex.search(text)
        if m:
            parts = dict(zip(order, (int(g) for g in m.groups()), strict=True))
            try:
                return date(parts["y"], parts["m"], parts["d"])
            except ValueError:
                return None
    for m in _WORD_DATE_RE.finditer(text):
        month = _MONTHS.get(m.group(2).casefold())
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(1)))
            except ValueError:
                return None
    return None


def parse_year(text: str) -> int | None:
    """Return the last plausible 4-digit year (e.g. ``March 2024`` -> 2024)."""
    years = _YEAR_RE.findall(normalize_text(text))
    return int(years[-1]) if years else None


def age_on(birth: date, as_of: date) -> int:
    return as_of.year - birth.year - ((as_of.month, as_of.day) < (birth.month, birth.day))


def parse_age(text: str, as_of: date | None = None) -> int | None:
    """Parse ``34 years`` / ``34 वर्ष`` or derive age from a date of birth."""
    text = normalize_text(text)
    birth = parse_date(text)
    if birth is not None:
        return age_on(birth, as_of or date.today())
    m = re.search(r"(?<!\d)(\d{1,3})(?!\d)", text)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- enums
# Ordered: more specific phrases first ("अनुसूचित जनजाति" contains "जाति").
_CATEGORY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("st", r"scheduled\s*tribes?|अनुसूचित\s*जन\s*जाति|\bS\.?\s?T\.?\b"),
    ("sc", r"scheduled\s*castes?|अनुसूचित\s*जाति|\bS\.?\s?C\.?\b"),
    (
        "obc",
        r"other\s*backward\s*class(?:es)?|अन्य\s*पिछड़ा\s*वर्ग|पिछड़ा\s*वर्ग|पिछडा\s*वर्ग|\bO\.?\s?B\.?\s?C\.?\b",
    ),
    ("ews", r"economically\s*weaker\s*sections?|आर्थिक\s*रूप\s*से\s*कमजोर|\bE\.?\s?W\.?\s?S\.?\b"),
    ("general", r"\bgeneral\b|unreserved|सामान्य|अनारक्षित"),
)
_GENDER_PATTERNS: tuple[tuple[str, str], ...] = (
    ("transgender", r"transgender|third\s*gender|ट्रांसजेंडर|तृतीय\s*लिंग|उभयलिंगी"),
    ("female", r"\bfemale\b|महिला|स्त्री|\bF\b"),
    ("male", r"\bmale\b|पुरुष|पुरूष|\bM\b"),
)
_BOARD_PATTERNS: tuple[tuple[str, str], ...] = (
    ("mpbse", r"\bmpbse\b|madhya\s*pradesh\s*board|board\s*of\s*secondary\s*education,?\s*madhya\s*pradesh|"
              r"माध्यमिक\s*शिक्षा\s*मण्?ं?डल|मा\.?\s*शि\.?\s*मं\.?"),
    ("cbse", r"\bcbse\b|central\s*board\s*of\s*secondary\s*education|केन्द्रीय\s*माध्यमिक\s*शिक्षा\s*बोर्ड"),
)  # fmt: skip
_RESIDENCE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("rural", r"\brural\b|ग्रामीण|gram\s*panchayat|ग्राम\s*पंचायत"),
    ("urban", r"\burban\b|शहरी|नगरीय|nagar\s*(?:nigam|palika|parishad)|नगर\s*(?:निगम|पालिका|परिषद)"),
)


def _first_pattern(text: str, patterns: tuple[tuple[str, str], ...]) -> str | None:
    text = normalize_text(text)
    for value, pattern in patterns:
        # Patterns are NFC-normalised like the text (nukta letters such as ड़ decompose under NFC).
        if re.search(unicodedata.normalize("NFC", pattern), text, re.IGNORECASE):
            return value
    return None


def parse_category(text: str) -> str | None:
    """Map a social category (``OBC``, ``अनुसूचित जनजाति`` ...) to ``general|ews|obc|sc|st``."""
    return _first_pattern(text, _CATEGORY_PATTERNS)


def parse_gender(text: str) -> str | None:
    return _first_pattern(text, _GENDER_PATTERNS)


def parse_board(text: str) -> str | None:
    """Return ``mpbse`` / ``cbse`` when named; ``None`` when no board is recognisable."""
    return _first_pattern(text, _BOARD_PATTERNS)


def parse_residence(text: str) -> str | None:
    return _first_pattern(text, _RESIDENCE_PATTERNS)


_STATE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Madhya Pradesh", r"madhya\s*pradesh|मध्य\s*प्रदेश|म\.?\s?प्र\.?"),
)


def parse_state(text: str) -> str | None:
    """Recognises Madhya Pradesh (the platform's only state); other states return ``None``."""
    return _first_pattern(text, _STATE_PATTERNS)


# --------------------------------------------------------------------------- names
_NAME_STOP = re.compile(
    r"\s+(?:s/o|d/o|w/o|c/o|son\s+of|daughter\s+of|wife\s+of|father|पिता|पति|पुत्र|पुत्री|"
    r"आत्मज|आत्मजा|age|आयु|gender|लिंग|district|जिला)(?=[\s.:,/']|$).*$",  # \b fails after matras
    re.IGNORECASE,
)
_HONORIFIC = re.compile(
    r"^(?:shri|shree|smt|sushri|kumari|kum|mr|mrs|ms|श्री|श्रीमती|सुश्री|कुमारी|कु)\.?\s+", re.I
)


def clean_name(text: str) -> str | None:
    """Strip honorifics, trailing relations (``S/o ...``) and punctuation from a person name."""
    text = normalize_text(text)
    text = _NAME_STOP.sub("", text)
    text = _HONORIFIC.sub("", text)
    if re.search(r"\d", text):
        return None
    text = "".join(ch if _is_letter(ch) or ch in " .'-" else " " for ch in text)
    text = re.sub(r"\s+", " ", text).strip(" .-'")
    if len(text) < 2:
        return None
    letters = sum(ch.isalpha() for ch in text)
    return text if letters >= 2 else None


# --------------------------------------------------------------------------- districts
@dataclass(frozen=True, slots=True)
class District:
    name: str
    aliases: tuple[str, ...]


# The 55 districts of Madhya Pradesh (incl. Maihar, Mauganj and Pandhurna, created in 2023),
# with common Hindi spellings and former names.
MP_DISTRICTS: tuple[District, ...] = (
    District("Agar Malwa", ("आगर मालवा", "आगर-मालवा", "Agar")),
    District("Alirajpur", ("अलीराजपुर", "आलीराजपुर")),
    District("Anuppur", ("अनूपपुर",)),
    District("Ashoknagar", ("अशोकनगर", "Ashok Nagar")),
    District("Balaghat", ("बालाघाट",)),
    District("Barwani", ("बड़वानी", "Badwani")),
    District("Betul", ("बैतूल",)),
    District("Bhind", ("भिंड", "भिण्ड")),
    District("Bhopal", ("भोपाल",)),
    District("Burhanpur", ("बुरहानपुर",)),
    District("Chhatarpur", ("छतरपुर",)),
    District("Chhindwara", ("छिंदवाड़ा", "छिन्दवाड़ा")),
    District("Damoh", ("दमोह",)),
    District("Datia", ("दतिया",)),
    District("Dewas", ("देवास",)),
    District("Dhar", ("धार",)),
    District("Dindori", ("डिंडोरी", "डिण्डोरी")),
    District("Guna", ("गुना",)),
    District("Gwalior", ("ग्वालियर",)),
    District("Harda", ("हरदा",)),
    District("Indore", ("इंदौर", "इन्दौर")),
    District("Jabalpur", ("जबलपुर",)),
    District("Jhabua", ("झाबुआ",)),
    District("Katni", ("कटनी",)),
    District("Khandwa", ("खंडवा", "खण्डवा", "East Nimar", "पूर्वी निमाड़")),
    District("Khargone", ("खरगोन", "West Nimar", "पश्चिमी निमाड़")),
    District("Maihar", ("मैहर",)),
    District("Mandla", ("मंडला", "मण्डला")),
    District("Mandsaur", ("मंदसौर", "मन्दसौर")),
    District("Mauganj", ("मऊगंज",)),
    District("Morena", ("मुरैना",)),
    District("Narmadapuram", ("नर्मदापुरम", "Hoshangabad", "होशंगाबाद")),
    District("Narsinghpur", ("नरसिंहपुर",)),
    District("Neemuch", ("नीमच",)),
    District("Niwari", ("निवाड़ी",)),
    District("Pandhurna", ("पांढुर्णा", "पांढुरना")),
    District("Panna", ("पन्ना",)),
    District("Raisen", ("रायसेन",)),
    District("Rajgarh", ("राजगढ़",)),
    District("Ratlam", ("रतलाम",)),
    District("Rewa", ("रीवा",)),
    District("Sagar", ("सागर",)),
    District("Satna", ("सतना",)),
    District("Sehore", ("सीहोर",)),
    District("Seoni", ("सिवनी",)),
    District("Shahdol", ("शहडोल",)),
    District("Shajapur", ("शाजापुर",)),
    District("Sheopur", ("श्योपुर",)),
    District("Shivpuri", ("शिवपुरी",)),
    District("Sidhi", ("सीधी",)),
    District("Singrauli", ("सिंगरौली",)),
    District("Tikamgarh", ("टीकमगढ़",)),
    District("Ujjain", ("उज्जैन",)),
    District("Umaria", ("उमरिया",)),
    District("Vidisha", ("विदिशा",)),
)

_DISTRICT_KEYS: dict[str, str] = {}
for _d in MP_DISTRICTS:
    for _alias in (_d.name, *_d.aliases):
        _DISTRICT_KEYS[match_key(_alias)] = _d.name


@dataclass(frozen=True, slots=True)
class DistrictMatch:
    name: str
    score: float  # 1.0 exact, < 1.0 fuzzy similarity


def match_district(text: str, min_score: float = 0.8) -> DistrictMatch | None:
    """Match the leading words of ``text`` to an MP district (exact, then fuzzy)."""
    words = normalize_text(text).replace(",", " ").split()
    best: DistrictMatch | None = None
    # Try 3-, 2- and 1-word prefixes so "Agar Malwa (M.P.)" and "Sehore" both work.
    for size in (3, 2, 1):
        if len(words) < size:
            continue
        key = match_key(" ".join(words[:size]))
        if not key:
            continue
        if key in _DISTRICT_KEYS:
            return DistrictMatch(_DISTRICT_KEYS[key], 1.0)
        for alias_key, name in _DISTRICT_KEYS.items():
            score = SequenceMatcher(None, key, alias_key).ratio()
            if score >= min_score and (best is None or score > best.score):
                best = DistrictMatch(name, round(score, 3))
    return best

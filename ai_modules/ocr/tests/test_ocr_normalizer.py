from datetime import date

import pytest

from ai_modules.ocr import normalizer as norm


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Rs. 1,20,000/-", 120000),
        ("₹ 2,50,000", 250000),
        ("रु. १,२०,०००/-", 120000),
        ("120000 रुपये", 120000),
        ("INR 1.5 lakh", 150000),
        ("1.20 लाख रुपये", 120000),
        ("Rs. 2 crore", 20000000),
        ("Rs. 4.16,000/-", 416000),  # OCR read the first grouping comma as a dot
        ("Rs. 4.16.000", 416000),
        ("Rs. 1,20,000.50", 120000.5),
        ("dated 12.03.2025", None),
        ("Certificate dated 12/03/2025 for Rs. 96,000", 96000),
        ("no amount here", None),
    ],
)
def test_parse_amount_inr(text, expected):
    assert norm.parse_amount_inr(text) == expected


def test_currency_marker_needs_word_boundary():
    # "years" ends in "rs" but is not a currency marker; the lakh-marked amount wins.
    assert norm.parse_amount_inr("for 5 years, income 1.2 lakh") == 120000


@pytest.mark.parametrize(
    ("text", "expected"),
    [("78.40%", 78.4), ("Percentage: 91 percent", 91), ("६५.५ प्रतिशत", 65.5), ("78.4", 78.4), ("140%", None)],
)
def test_parse_percentage(text, expected):
    assert norm.parse_percentage(text) == expected


def test_percentage_from_marks():
    assert norm.parse_marks_ratio("Grand Total 392 / 500") == (392, 500)
    assert norm.parse_marks_ratio("392 out of 500") == (392, 500)
    assert norm.percentage_from_marks(392, 500) == 78.4
    assert norm.percentage_from_marks(501, 500) is None
    assert norm.percentage_from_marks(10, 0) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12/05/2007", date(2007, 5, 12)),
        ("12-05-2007", date(2007, 5, 12)),
        ("2007-05-12", date(2007, 5, 12)),
        ("12 May 2007", date(2007, 5, 12)),
        ("१२ मई २००७", date(2007, 5, 12)),
        ("31/02/2007", None),
        ("no date", None),
    ],
)
def test_parse_date(text, expected):
    assert norm.parse_date(text) == expected


def test_age_and_year():
    as_of = date(2025, 5, 11)
    assert norm.parse_age("12/05/2007", as_of) == 17
    assert norm.parse_age("12/05/2007", date(2025, 5, 12)) == 18
    assert norm.parse_age("18 Years", as_of) == 18
    assert norm.parse_age("१८ वर्ष", as_of) == 18
    assert norm.parse_year("Higher Secondary Examination March 2024") == 2024
    assert norm.parse_year("no year") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Other Backward Class (OBC)", "obc"),
        ("अन्य पिछड़ा वर्ग", "obc"),
        ("अनुसूचित जनजाति", "st"),
        ("अनुसूचित जाति", "sc"),
        ("Scheduled Tribe", "st"),
        ("Scheduled Caste (SC)", "sc"),
        ("S.C.", "sc"),
        ("EWS", "ews"),
        ("सामान्य", "general"),
        ("Unreserved", "general"),
        ("1st Division", None),
        ("State of Madhya Pradesh", None),
    ],
)
def test_parse_category(text, expected):
    assert norm.parse_category(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Female", "female"),
        ("महिला", "female"),
        ("पुरुष", "male"),
        ("M", "male"),
        ("Transgender", "transgender"),
    ],
)
def test_parse_gender(text, expected):
    assert norm.parse_gender(text) == expected


def test_board_residence_state():
    assert norm.parse_board("Board of Secondary Education, Madhya Pradesh, Bhopal") == "mpbse"
    assert norm.parse_board("माध्यमिक शिक्षा मण्डल, मध्यप्रदेश") == "mpbse"
    assert norm.parse_board("Central Board of Secondary Education") == "cbse"
    assert norm.parse_board("State Board of Rajasthan") is None
    assert norm.parse_residence("ग्रामीण") == "rural"
    assert norm.parse_residence("Nagar Nigam Bhopal") == "urban"
    assert norm.parse_state("मध्य प्रदेश") == "Madhya Pradesh"
    assert norm.parse_state("Madhya Pradesh") == "Madhya Pradesh"
    assert norm.parse_state("Rajasthan") is None


def test_district_list_is_complete():
    assert len(norm.MP_DISTRICTS) == 55
    assert len({d.name for d in norm.MP_DISTRICTS}) == 55


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Sehore", "Sehore"),
        ("सीहोर (म.प्र.)", "Sehore"),
        ("छिंदवाड़ा", "Chhindwara"),
        ("Hoshangabad", "Narmadapuram"),
        ("Agar Malwa", "Agar Malwa"),
        ("SEHORE, MADHYA PRADESH", "Sehore"),
    ],
)
def test_match_district_exact(text, expected):
    match = norm.match_district(text)
    assert match is not None and match.name == expected and match.score == 1.0


def test_match_district_fuzzy_and_unknown():
    fuzzy = norm.match_district("Sehor")
    assert fuzzy is not None and fuzzy.name == "Sehore" and 0.8 <= fuzzy.score < 1
    assert norm.match_district("Jaipur") is None
    assert norm.match_district("Collector Office") is None


def test_clean_name():
    assert norm.clean_name("Shri Ramesh Kumar S/o Mohan Lal") == "Ramesh Kumar"
    assert norm.clean_name("श्रीमती सुनीता वर्मा पति राम वर्मा") == "सुनीता वर्मा"
    assert norm.clean_name("12345") is None
    assert norm.clean_name(" : ") is None

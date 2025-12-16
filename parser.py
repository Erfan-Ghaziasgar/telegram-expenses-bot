# parser.py
import re
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

FA_TO_EN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

RECEIVABLE_PATTERNS = [
    r"باید\s*بهم\s*بده",
    r"باید\s*به\s*من\s*بده",
    r"باید\s*بهم\s*پرداخت\s*کنه",
    r"بهم\s*بدهکار(?:ه|است)?",
]
PAYABLE_PATTERNS = [
    r"باید\s*بدم",
    r"باید\s*به\s*\S+\s*بدم",
    r"باید\s*پرداخت\s*کنم",
    r"بدهکار(?:م|هستم)?",
]

AMOUNT_RE = re.compile(r"(?<!\d)(\d{1,12})(?!\d)")  # after digit normalization
SHORT_PAYABLE_RE = re.compile(r"^\s*\d{1,12}\s*(?:تومن|تومان|ریال)?\s*به\s+[^\s]+\s*$")

@dataclass
class Parsed:
    amount: int
    direction: str  # expense | payable | receivable
    person: Optional[str]
    description: str
    raw: str

def normalize_digits(text: str) -> str:
    return text.translate(FA_TO_EN_DIGITS).translate(AR_TO_EN_DIGITS)

def extract_amount(text: str) -> Optional[int]:
    m = AMOUNT_RE.search(text)
    if not m:
        return None
    return int(m.group(1))

def detect_direction(text: str) -> str:
    # Check receivable first (more specific)
    for pat in RECEIVABLE_PATTERNS:
        if re.search(pat, text):
            return "receivable"
    for pat in PAYABLE_PATTERNS:
        if re.search(pat, text):
            return "payable"
    # Shorthand: "<amount> به <name>" means you owe them
    if SHORT_PAYABLE_RE.search(text):
        return "payable"
    return "expense"

def extract_person(text: str, direction: str) -> Optional[str]:
    # Heuristic 1: "به <name>" pattern for payable
    if direction == "payable":
        m = re.search(r"به\s+([^\s]+)", text)
        if m:
            return m.group(1)

    # Heuristic 2: "<name> باید ..." pattern for receivable/payable
    m = re.search(r"([^\s]+)\s+باید", text)
    if m:
        candidate = m.group(1)
        # Avoid grabbing generic words
        if candidate not in ("من", "یه", "یک", "این", "اون", "او", "ایشون"):
            return candidate

    return None

def clean_description(text: str, amount: int) -> str:
    # remove the first occurrence of the amount and common currency words if present
    t = text
    t = re.sub(rf"(?<!\d){amount}(?!\d)", "", t, count=1).strip()
    t = re.sub(r"\b(تومن|تومان|ریال)\b", "", t).strip()
    return re.sub(r"\s{2,}", " ", t)

def parse_message(raw_text: str) -> Dict[str, Any]:
    norm = normalize_digits(raw_text)
    amount = extract_amount(norm)
    if amount is None:
        raise ValueError("No amount found in message")

    direction = detect_direction(norm)
    person = extract_person(norm, direction)
    description = clean_description(norm, amount)

    return asdict(Parsed(
        amount=amount,
        direction=direction,
        person=person,
        description=description,
        raw=raw_text
    ))

if __name__ == "__main__":
    tests = [
        "100 تومن پول نون",
        "۲۲۰ به ممد",
        "220 تومن به ممد باید بدم",
        "150 تومن ممد باید بهم بده",
        "۱۵۰ تومن ممد باید بهم بده",
    ]
    for t in tests:
        print(parse_message(t))

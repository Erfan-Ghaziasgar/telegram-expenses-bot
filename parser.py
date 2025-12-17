# parser.py
import re
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

FA_TO_EN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

RECEIVABLE_PATTERNS = [
    # must pay me
    r"باید\s*(?:بهم|به\s*من)\s*بده",
    r"باید\s*پول(?:ش)?\s*(?:رو)?\s*(?:بهم|به\s*من)\s*بده",
    # owes me
    r"(?:بهم|به\s*من)\s*بدهکار(?:ه|است)?",
    r"بدهکار(?:ه|است)?\s*(?:بهم|به\s*من)",
    # I have a claim
    r"از\s*\S+\s*طلب\s*دارم",
    r"طلب(?:کار)?(?:م|ه)?",
    r"طلبم\s*از\s*\S+",
    # someone must pay
    r"\S+\s*باید\s*بده",
    r"\S+\s*باید\s*پول\s*بده",
    # supposed to pay
    r"قراره\s*(?:بهم|به\s*من)\s*بده",
    r"قرار(?:ه|بود)?\s*پول\s*بده",
    # transfer to me
    r"باید\s*(?:بهم|به\s*من)\s*واریز\s*کنه",
    r"واریز\s*کن(?:ه)?",
    # settle by paying me
    r"بده\s*حساب\s*شه",
]

PAYABLE_PATTERNS = [
    # must pay
    r"باید\s*بدم",
    r"باید\s*پول\s*بدم",
    # must pay someone
    r"باید\s*به\s*\S+\s*بدم",
    r"باید\s*پول(?:ش)?\s*رو\s*به\s*\S+\s*بدم",
    # I owe
    r"بدهکار(?:م|هستم)?",
    r"من\s*بدهکار(?:م|هستم)?",
    # borrowed money
    r"قرض\s*گرفتم",
    r"از\s*\S+\s*قرض\s*گرفتم",
    # settle payment
    r"باید\s*تسویه\s*کنم",
    r"تسویه\s*حساب",
    # pay / transfer
    r"باید\s*پرداخت\s*کنم",
    r"باید\s*واریز\s*کنم",
    r"واریز\s*بدم",
    # settle by paying
    r"بدم\s*حساب\s*شه",
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
    """
    Extract the intended amount from text.

    We intentionally skip common "list index" prefixes like:
      - "2. ..." / "۲. ..." / "2٫ ..." / "2.edit ..."
    so users don't accidentally save the index number instead of the real amount.
    """
    matches = list(AMOUNT_RE.finditer(text))
    if not matches:
        return None

    for m in matches:
        start = m.start()
        end = m.end()
        prefix = text[:start].strip()
        if prefix:
            return int(m.group(1))

        if end < len(text):
            next_ch = text[end]
            next_next = text[end + 1] if end + 1 < len(text) else ""
            if next_ch in {".", "٫"} and (next_next.isspace() or next_next.isalpha()):
                continue

        return int(m.group(1))

    return int(matches[0].group(1))

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

    # Heuristic 2: "از <name>" pattern for receivable/payable (e.g. "از ممد طلب دارم" / "از ممد قرض گرفتم")
    m = re.search(r"از\s+([^\s]+)", text)
    if m:
        candidate = m.group(1)
        if candidate not in ("من", "خودم", "خودمون", "خودت", "خودتون"):
            return candidate

    # Heuristic 3: "<name> باید ..." pattern for receivable/payable
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
    # drop common list-index prefixes like "2. ..." / "۲. ..."
    t = re.sub(r"^\s*\d+\s*[.٫]\s*", "", t).strip()
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

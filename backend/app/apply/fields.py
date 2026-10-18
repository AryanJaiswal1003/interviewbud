"""What kind of thing an application form field asks for, which decides who may fill it.

* contact / file: filled from the candidate's application details (name, email, resume...).
* question: answered from what the candidate said (screening call, confirmed facts, approved drafts).
* consent: a box the candidate ticks themselves in the preview; never pre-ticked for them.
* voluntary: equal-opportunity self-identification (gender, race, veteran, disability...). Never auto-filled; the
  candidate may choose an answer themselves.
* sensitive: government IDs, date of birth, passwords, bank details. Never filled by us at all.
"""

import re

VOLUNTARY = re.compile(r"\b(gender|sex\b|race|racial|ethnic|hispanic|latin[oax]|veteran|disabilit|pronoun|sexual "
                       r"orientation|transgender|lgbt|self[- ]identif|demographic|eeo|age range|religio)", re.I)
SENSITIVE = re.compile(r"\b(social security|ssn|national insurance|passport|aadhaar|pan card|tax id|driver'?s licen[cs]e "
                       r"number|date of birth|birth ?date|\bdob\b|password|bank|routing number|iban|credit card)", re.I)
CONSENT = re.compile(r"\b(i agree|i consent|i acknowledge|i certify|i confirm|privacy (policy|notice)|terms of|"
                     r"consent to|acknowledg)", re.I)

CONTACT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("preferred_name", re.compile(r"preferred (first )?name", re.I)),
    ("first_name", re.compile(r"\b(first|given) ?name\b|^first$", re.I)),
    ("last_name", re.compile(r"\b(last|family|sur) ?name\b|^last$", re.I)),
    ("full_name", re.compile(r"^(full )?name\b|^your name|legal name", re.I)),
    ("email", re.compile(r"e-?mail", re.I)),
    ("phone", re.compile(r"phone|mobile|telephone", re.I)),
    ("linkedin", re.compile(r"linked ?in", re.I)),
    ("github", re.compile(r"git ?hub", re.I)),
    ("website", re.compile(r"website|portfolio|personal (site|url)|blog", re.I)),
    ("location", re.compile(r"^(current )?(location|city)\b|where are you (currently )?(based|located)\b(?!.*\?)", re.I)),
]


def contact_key(label: str, name: str = "", autocomplete: str = "") -> str | None:
    hint = f"{label} {name}".strip()
    auto = (autocomplete or "").lower()
    by_auto = {"given-name": "first_name", "family-name": "last_name", "name": "full_name", "email": "email",
               "tel": "phone", "url": "website"}
    if auto in by_auto:
        return by_auto[auto]
    low_name = (name or "").lower()
    for key in ("first_name", "last_name", "preferred_name", "email", "phone"):
        if low_name == key or low_name.endswith(f"[{key}]"):
            return key
    for key, pat in CONTACT_PATTERNS:
        if pat.search(label or "") or (not label and pat.search(hint)):
            return key
    return None


def classify(field: dict) -> str:
    label = field.get("label", "")
    text = f"{label} {field.get('description', '')}"
    if field.get("type") == "file":
        return "file"
    if SENSITIVE.search(text):
        return "sensitive"
    if VOLUNTARY.search(text) or field.get("section") == "demographic":
        return "voluntary"
    if field.get("type") == "checkbox" and CONSENT.search(text):
        return "consent"
    if contact_key(label, field.get("name", ""), field.get("autocomplete", "")):
        return "contact"
    return "question"

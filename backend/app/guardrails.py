"""Guardrails: PII redaction for anything sent to the LLM or written to logs."""
import re

PII_PATTERNS = {
    "EMAIL": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "CARD": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "PHONE": re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)"),
}


def redact(text: str) -> str:
    # ponytail: regex PII only; swap in Presidio if you need names/addresses
    for label, pat in PII_PATTERNS.items():
        text = pat.sub(f"[{label}]", text)
    return text


MAX_QUESTION_CHARS = 4000


def check_input(question: str) -> str:
    q = question.strip()
    if not q:
        raise ValueError("Empty question")
    if len(q) > MAX_QUESTION_CHARS:
        raise ValueError(f"Question too long (max {MAX_QUESTION_CHARS} chars)")
    return redact(q)

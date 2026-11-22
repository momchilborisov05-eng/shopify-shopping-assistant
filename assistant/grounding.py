"""
The grounding guard: every number in an answer must come from a tool result or from the shopper.

The system prompt already tells Claude never to state a price, stock level, delivery time or policy
figure that a tool didn't return. This module checks it after the fact, on every answer, in code:

1. ``extract_numbers`` finds each number in the answer and labels what it is from the words around it:
   ``price`` (€189, 29,90 лв), ``percent`` (20 %), ``stock`` (only 2 left), ``duration`` (14 days), ``date``
   (2026-11-04) or ``other``. List markers, times of day inside a quoted range and numbers glued to letters
   (SKUs, tracking numbers) are skipped.
2. The allowed values are every number in this conversation's tool results (the raw JSON) and in the
   shopper's own messages ("under €200", "size 42").
3. A number that is not allowed is a violation. Bare small counts ("here are 3 options") are not checked.

``enforce`` then removes the sentences that carry a violation, so the shopper never sees an invented
figure, and the turn is flagged on the dashboard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CURRENCY = r"(?:€|\$|£|eur\b|euros?\b|usd\b|лв\.?|лева|евро)"
NUMBER = re.compile(r"(?<![\w.,/:#№])(?<![^\W\d_]-)(\d{1,3}(?:[,  ]\d{3})+|\d+)(?:([.,])(\d{1,2})(?!\d))?(?![\w])")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
LIST_MARKER = re.compile(r"^\s*\d{1,2}[.)]\s", re.M)
URL = re.compile(r"\]\([^)]*\)|https?://\S+")
KIND_AFTER = [
    ("percent", re.compile(r"^\s*(?:%|percent|процента)", re.I)),
    ("price", re.compile(rf"^\s*{CURRENCY}", re.I)),
    ("stock", re.compile(r"^\s*(?:left|in stock|available|units?|pieces?|pcs|items? left|бр|броя|остава|налични)",
                         re.I)),
    ("duration", re.compile(r"^\s*(?:-\s*\d+\s*)?(?:business |working |calendar )?(?:days?|hours?|weeks?|months?|"
                            r"years?|минути|minutes?|дни|дена|часа|седмици|месеца|години)", re.I)),
]
KIND_BEFORE = [("price", re.compile(rf"{CURRENCY}\s*$", re.I)),
               ("stock", re.compile(r"(?:only|just|остават|само)\s*$", re.I))]
SMALL_COUNT = 5  # "here are 3 options": unchecked when the number names nothing the store sets


@dataclass
class NumberMention:
    text: str
    value: float
    kind: str
    start: int


@dataclass
class GroundingReport:
    checked: list[NumberMention] = field(default_factory=list)
    violations: list[NumberMention] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict:
        return {"checked": len(self.checked),
                "violations": [{"text": v.text, "kind": v.kind} for v in self.violations]}


def _value(whole: str, sep: str | None, frac: str | None) -> float:
    digits = re.sub(r"[,  ]", "", whole)
    return float(f"{digits}.{frac}") if sep and frac else float(digits)


def extract_numbers(text: str) -> list[NumberMention]:
    """Every number in the text with its kind. A date's year, month and day are three mentions of kind date."""
    masked = URL.sub(lambda m: " " * len(m.group(0)), text or "")
    masked = LIST_MARKER.sub(lambda m: " " * len(m.group(0)), masked)
    out = []
    dates = [(m.start(), m.end()) for m in DATE.finditer(masked)]
    for m in NUMBER.finditer(masked):
        whole, sep, frac = m.groups()
        # "1,299" is a thousand separator; "29,90" is a decimal comma (frac has 2 digits, no 3-digit group).
        value = _value(whole, sep, frac)
        if any(a <= m.start() < b for a, b in dates):
            kind = "date"
        else:
            after, before = masked[m.end():m.end() + 24], masked[max(0, m.start() - 12):m.start()]
            kind = next((k for k, rx in KIND_AFTER if rx.search(after)), None) or \
                next((k for k, rx in KIND_BEFORE if rx.search(before)), "other")
        out.append(NumberMention(m.group(0), value, kind, m.start()))
    return out


def allowed_values(sources: list[str]) -> set[float]:
    values: set[float] = set()
    for text in sources:
        for m in re.finditer(r"\d+(?:[.,]\d+)?", text or ""):
            raw = m.group(0)
            values.add(round(float(raw.replace(",", ".")) if raw.count(",") == 1 and len(raw.split(",")[1]) <= 2
                             else float(raw.replace(",", "")), 2))
        for m in NUMBER.finditer(text or ""):
            values.add(round(_value(*m.groups()), 2))
    return values


def check(answer: str, sources: list[str]) -> GroundingReport:
    allowed = allowed_values(sources)
    report = GroundingReport()
    for mention in extract_numbers(answer):
        if mention.kind == "other" and mention.value <= SMALL_COUNT and mention.value.is_integer():
            continue
        report.checked.append(mention)
        if round(mention.value, 2) not in allowed:
            report.violations.append(mention)
    return report


BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n+")
LIST_ONLY = re.compile(r"^\s*(?:[-*]\s*)?\d{1,2}[.)]\s*$")
FALLBACK = {"en": "I couldn't confirm that detail from the store's data. Shall I check it again or pass your "
                  "question to the team?",
            "bg": "Не успях да потвърдя тази информация от данните на магазина. Да проверя ли отново, или да "
                  "предам въпроса на колега?"}


def enforce(answer: str, report: GroundingReport, lang: str = "en") -> str:
    """The answer without the sentences that carry an ungrounded number (and a note when something went)."""
    if report.ok:
        return answer
    bad = {v.start for v in report.violations}
    spans, start = [], 0
    for m in BOUNDARY.finditer(answer):
        spans.append((start, m.end()))
        start = m.end()
    spans.append((start, len(answer)))
    merged: list[tuple[int, int]] = []
    for a, b in spans:  # "2. It has..." : a list marker belongs to the sentence after it
        if merged and LIST_ONLY.match(answer[merged[-1][0]:merged[-1][1]]):
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    spans = merged
    kept = [answer[a:b] for a, b in spans if not any(a <= x < b for x in bad)]
    rest = "".join(kept).strip()
    note = FALLBACK.get(lang, FALLBACK["en"])
    return f"{rest}\n\n{note}" if rest else note


def sources_from_history(history: list) -> list[str]:
    """Tool results and the shopper's own words: the only places an answer's numbers may come from."""
    out = []
    for message in history:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            out.append(content)
            continue
        for block in content or []:
            if block.get("type") == "tool_result":
                c = block.get("content")
                out.append(c if isinstance(c, str) else str(c))
            elif block.get("type") == "text":
                out.append(block.get("text", ""))
    return out

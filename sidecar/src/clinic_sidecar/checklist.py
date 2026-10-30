"""Live checklist: which parts of an admission interview have come up so far.

Plain keyword rules over the live transcript, not a model: instant, no extra
memory while recording, and every tick can say exactly which words triggered it.
It only nudges ("allergies not asked yet"); nothing here goes into the record.

A topic counts as covered as soon as anyone mentions it, question or answer.
German patterns first, a few English ones for mixed-language consultations.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Topic:
    id: str
    label: str
    patterns: tuple[str, ...]


TOPICS: tuple[Topic, ...] = (
    Topic(
        "complaint",
        "Reason for visit",
        (r"was führt sie", r"beschwerden", r"was ist passiert", r"warum sind sie", r"schmerz", r"what brings you"),
    ),
    Topic(
        "onset",
        "Onset and duration",
        (r"seit wann", r"wie lange", r"\bseit\b", r"\bsince\b", r"how long"),
    ),
    Topic(
        "medication",
        "Current medication",
        (r"medikament", r"tablette", r"nehmen sie", r"\bnehme\b", r"\bmg\b", r"milligramm", r"medication", r"\bpills?\b"),
    ),
    Topic("allergies", "Allergies", (r"allergi", r"unverträglich", r"allergic")),
    Topic(
        "history",
        "Previous illnesses",
        (r"vorerkrank", r"erkrankung", r"krankheiten", r"bekannt", r"diabetes", r"bluthochdruck", r"herzinfarkt", r"medical history"),
    ),
    Topic("surgery", "Previous operations", (r"operation", r"operiert", r"\bop\b", r"eingriff", r"surgery")),
    Topic(
        "substances",
        "Smoking and alcohol",
        (r"rauch", r"raucher", r"zigarett", r"alkohol", r"trinken sie", r"smok", r"alcohol"),
    ),
    Topic(
        "living",
        "Living situation and care",
        (r"wohn", r"leben sie", r"allein", r"pflege", r"angehörig", r"versorg", r"live alone", r"\bcare\b"),
    ),
)

_COMPILED = {t.id: [re.compile(p, re.IGNORECASE) for p in t.patterns] for t in TOPICS}


@dataclass
class ChecklistItem:
    id: str
    label: str
    covered: bool
    line: int | None  # first live line that mentions it (1-based)
    match: str | None  # the words that matched


def evaluate(lines: list[str]) -> list[ChecklistItem]:
    items = []
    for topic in TOPICS:
        hit = None
        for n, text in enumerate(lines, start=1):
            m = next((m for rx in _COMPILED[topic.id] if (m := rx.search(text))), None)
            if m:
                hit = (n, m.group(0))
                break
        items.append(
            ChecklistItem(topic.id, topic.label, hit is not None, hit[0] if hit else None, hit[1] if hit else None)
        )
    return items

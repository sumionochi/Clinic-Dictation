"""What the agents extract from a consultation.

Lists and strings have hard maximums. Ollama turns the schema into a grammar, so a
small model physically cannot loop forever adding items or extending a quote.

A flat list of facts rather than a nested form: small local models fill flat
structures far more reliably, and each fact can be checked on its own.

The extractor never infers. A fact exists only if someone in the room said it,
and it points at the exact words. Anything without evidence stays out.
"""

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["complaint", "symptom", "medication", "allergy", "condition", "surgery", "social"]
# present: affirmed / currently true ("ich nehme Ramipril", "Brustschmerzen")
# absent:  explicitly denied ("kein Fieber", "keine weiteren Allergien")
# past:    was true, no longer ("ehemaliger Raucher", "Metformin abgesetzt")
Assertion = Literal["present", "absent", "past"]


class Evidence(BaseModel):
    line: int = Field(description="Number of the transcript line the quote is from")
    quote: str = Field(max_length=160, description="Words copied exactly from that line, no paraphrase")


class Fact(BaseModel):
    category: Category
    name: str = Field(
        max_length=60, description="Short German name as said, e.g. 'Ramipril', 'Brustschmerzen', 'Penicillin'"
    )
    details: str | None = Field(
        default=None, max_length=120, description="Dose, frequency, duration or severity as said, else null"
    )
    assertion: Assertion
    evidence: list[Evidence] = Field(min_length=1, max_length=4)


MAX_FACTS = 60  # far more than a 15-minute admission interview holds


class Facts(BaseModel):
    facts: list[Fact] = Field(max_length=MAX_FACTS)


class PlainFact(BaseModel):
    """The baseline's facts: same fields, no evidence."""

    category: Category
    name: str = Field(max_length=60)
    details: str | None = Field(default=None, max_length=120)
    assertion: Assertion


class PlainFacts(BaseModel):
    facts: list[PlainFact] = Field(max_length=MAX_FACTS)


Verdict = Literal["supported", "wrong_assertion", "wrong_details", "unsupported"]


class Verification(BaseModel):
    # Reason first: the model writes fields in order, and a verdict written before
    # the reasoning gets committed blind ("denied, so 'absent' is right" followed
    # a verdict of wrong_assertion in testing).
    reason: str = Field(max_length=300, description="What the lines say, in one or two short sentences")
    verdict: Verdict


class TranscriptLine(BaseModel):
    line: int
    speaker: str | None  # display name, e.g. "Doctor", "Patient", or None if unknown
    text: str

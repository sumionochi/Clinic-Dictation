"""Prompts for the extraction, verification and baseline calls.

Written in English (models follow English instructions most reliably) about a
German transcript; extracted names stay in German as spoken.
"""

import json

from clinic_sidecar.agents.schema import Fact, TranscriptLine

_RULES = """\
Rules:
1. Only facts that were actually said. Never infer, never diagnose, never add
   anything from medical knowledge.
2. Facts are about the patient and come from what the patient (or a companion)
   says. A question alone is not a fact: "Haben Sie Fieber?" with no answer is
   nothing. If the patient answers "Nein", the fact is Fieber with assertion
   "absent".
3. assertion:
   - "present": affirmed or currently true ("ich nehme Ramipril", "Husten")
   - "absent": explicitly denied ("kein Fieber", "keine weiteren Allergien")
   - "past": was true, no longer ("ehemaliger Raucher", "habe ich abgesetzt")
4. One fact per medication, allergy, symptom, condition or operation.
   details: dose, frequency, duration or severity exactly as said, else null.
5. Names in German as spoken, short: "Ramipril", "Brustschmerzen", "Penicillin".
   "Keine (weiteren) Allergien" is category allergy, name "weitere Allergien",
   assertion "absent".
6. category:
   - complaint: the main reason for the visit (usually one fact)
   - symptom, medication, allergy
   - condition: known or previous illnesses
   - surgery: previous operations
   - social: smoking, alcohol, living situation, care at home
"""

EXTRACT_SYSTEM = f"""\
You extract clinical facts from a transcript of a German doctor-patient
conversation (an admission interview). Each line is numbered and shows who spoke.

{_RULES}
7. Every fact needs evidence: the line number and a few words copied EXACTLY
   from that line. For an answer like "Nein", cite both the question line and
   the answer line.
"""

BASELINE_SYSTEM = f"""\
You read a transcript of a German doctor-patient conversation (an admission
interview) and list the clinical facts in it.

{_RULES}"""

VERIFY_SYSTEM = """\
You check one claim extracted from a German doctor-patient conversation against
the transcript lines it was taken from. Judge only from these lines.

- "supported": the lines say exactly what the claim says about the patient.
  A denial supports a NOT claim: "Haben Sie Fieber?" / "Nein." supports
  "The patient does NOT have: Fieber". "kein Apixaban" supports "does NOT take:
  Apixaban". A medication the patient stopped supports "had / took before".
- "wrong_assertion": the lines are about this thing, but the claim gets it the
  wrong way round: claims they have it when they denied it, claims NOT when they
  have it, or claims current when they stopped it.
- "wrong_details": right fact, but dose, frequency or duration is not what
  was said.
- "unsupported": the lines do not say this, only the doctor asked without an
  answer, or it is about someone other than the patient.
"""


def format_transcript(lines: list[TranscriptLine]) -> str:
    return "\n".join(f"{ln.line} | {ln.speaker or 'Unknown'}: {ln.text}" for ln in lines)


def extract_user(lines: list[TranscriptLine]) -> str:
    return "Transcript:\n" + format_transcript(lines)


def retry_user(lines: list[TranscriptLine], rejected: list[tuple[Fact, str]]) -> str:
    listed = "\n".join(
        f"- {json.dumps(f.model_dump(), ensure_ascii=False)}\n  Problem: {reason}" for f, reason in rejected
    )
    return (
        "Transcript:\n"
        + format_transcript(lines)
        + "\n\nA checker rejected these facts you extracted earlier:\n"
        + listed
        + "\n\nExtract them again, corrected, with exact quotes. Leave a fact out if the"
        " transcript does not support it. Return only the corrected facts."
    )


_CLAIM = {
    "present": "The patient has / takes / reports: {name}",
    "absent": "The patient does NOT have / take: {name} (they denied it)",
    "past": "The patient had / took {name} before, but no longer",
}


def claim(fact: Fact) -> str:
    """The fact as a plain sentence. Models misread a bare "assertion": "absent"
    as "not mentioned"; a sentence with an explicit NOT is judged correctly."""
    text = _CLAIM[fact.assertion].format(name=fact.name)
    if fact.details:
        text += f" ({fact.details})"
    return f"[{fact.category}] {text}"


def verify_user(context: list[TranscriptLine], fact: Fact) -> str:
    return (
        "Transcript lines:\n"
        + format_transcript(context)
        + "\n\nClaim to check:\n"
        + claim(fact)
        + "\n\nWrite your reason first, then the verdict."
    )

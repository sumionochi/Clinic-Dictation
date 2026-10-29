from clinic_sidecar.agents.evidence import cited_context, evidence_problems, relocate_evidence
from clinic_sidecar.agents.graph import extract_facts, finalize, find_contradictions
from clinic_sidecar.agents.llm import CallLog
from clinic_sidecar.agents.schema import Evidence, Fact, Facts, TranscriptLine, Verification

LINES = [
    TranscriptLine(line=1, speaker="Doctor", text="Nehmen Sie Medikamente?"),
    TranscriptLine(line=2, speaker="Patient", text="Ja, Ramipril fünf Milligramm morgens."),
    TranscriptLine(line=3, speaker="Doctor", text="Haben Sie Fieber?"),
    TranscriptLine(line=4, speaker="Patient", text="Nein."),
]


def fact(name: str, assertion: str = "present", category: str = "medication", ev=((2, "Ramipril"),), details=None) -> Fact:
    return Fact(
        category=category,
        name=name,
        details=details,
        assertion=assertion,
        evidence=[Evidence(line=n, quote=q) for n, q in ev],
    )


class ScriptedLLM:
    """Answers each call name from a queue; verify answers by fact name."""

    model = "scripted"

    def __init__(self, extract: list[list[Fact]], verdicts: dict[str, list[str]]) -> None:
        self.extract = list(extract)
        self.verdicts = {k: list(v) for k, v in verdicts.items()}
        self.log = CallLog()

    def structured(self, name, system, user, schema, max_tokens=4000):
        self.log.calls += 1
        self.log.names.append(name)
        if name in ("extract", "retry"):
            return Facts(facts=self.extract.pop(0))
        claim_text = user.split("Claim to check:\n")[1]
        fact_name = next(k for k in self.verdicts if k in claim_text)
        return Verification(verdict=self.verdicts[fact_name].pop(0), reason="scripted")


def test_evidence_check() -> None:
    lines = {ln.line: ln for ln in LINES}
    assert evidence_problems(fact("Ramipril", ev=((2, "ramipril fünf milligramm"),)), lines) == []
    assert "not on line 2" in evidence_problems(fact("Ramipril", ev=((2, "Ramipril 10 mg"),)), lines)[0]
    assert "does not exist" in evidence_problems(fact("Ramipril", ev=((9, "x"),)), lines)[0]


def test_cited_context_includes_the_question() -> None:
    lines = {ln.line: ln for ln in LINES}
    assert [ln.line for ln in cited_context([Evidence(line=4, quote="Nein")], lines)] == [3, 4]


def test_good_facts_pass_in_one_round() -> None:
    fever = fact("Fieber", "absent", "symptom", ev=((3, "Fieber"), (4, "Nein")))
    llm = ScriptedLLM([[fact("Ramipril"), fever]], {"Ramipril": ["supported"], "Fieber": ["supported"]})
    result = extract_facts(LINES, llm)
    assert [f.name for f in result.facts] == ["Ramipril", "Fieber"]
    assert result.rejected == []
    assert result.rounds == 1
    assert llm.log.names == ["extract", "verify", "verify"]


def test_made_up_quote_is_sent_back_and_fixed() -> None:
    bad = fact("Ramipril", ev=((2, "Ramipril zehn Milligramm"),))
    good = fact("Ramipril", ev=((2, "Ramipril fünf Milligramm"),))
    llm = ScriptedLLM([[bad], [good]], {"Ramipril": ["supported"]})
    result = extract_facts(LINES, llm)
    assert [f.evidence[0].quote for f in result.facts] == ["Ramipril fünf Milligramm"]
    assert llm.log.names == ["extract", "retry", "verify"]


def test_wrong_assertion_retried_then_rejected_with_reason() -> None:
    fever = fact("Fieber", "present", "symptom", ev=((3, "Fieber"), (4, "Nein")))
    llm = ScriptedLLM([[fever], [fever]], {"Fieber": ["wrong_assertion", "wrong_assertion"]})
    result = extract_facts(LINES, llm)
    assert result.facts == []
    assert len(result.rejected) == 1
    assert "wrong_assertion" in result.rejected[0].reason
    assert result.rounds == 2  # one retry, then stop


def test_finalize_merges_duplicates() -> None:
    a = fact("Metformin", "present", ev=((2, "Ramipril"),))
    b = fact("metformin", "present", ev=((1, "Medikamente"),))
    c = fact("Metformin", "past", ev=((4, "Nein"),))
    facts = finalize([a, b, c])
    assert len(facts) == 2
    assert len(facts[0].evidence) == 2
    contradictions = find_contradictions([a, b, c])
    assert contradictions[0].assertions == ["past", "present"]
    assert contradictions[0].lines == [1, 2, 4]


def test_miscounted_line_is_corrected() -> None:
    lines = {ln.line: ln for ln in LINES}
    fixed, notes = relocate_evidence(fact("Fieber", "absent", "symptom", ev=((3, "Nein"),)), lines)
    assert fixed.evidence[0].line == 4
    assert notes == ["'Nein' moved from line 3 to 4"]
    # Made-up words stay where they are and fail the check.
    fixed, notes = relocate_evidence(fact("Ramipril", ev=((2, "Ramipril zehn mg"),)), lines)
    assert notes == [] and evidence_problems(fixed, lines)


def test_contradiction_survives_verifier_rejecting_one_side() -> None:
    stopped = fact("Metoprolol", "past", ev=((2, "Ramipril"),))
    taking = fact("Metoprolol", "present", ev=((4, "Nein"),))
    llm = ScriptedLLM([[stopped, taking], []], {"Metoprolol": ["wrong_assertion", "supported"]})
    result = extract_facts(LINES, llm)
    assert [c.name for c in result.contradictions] == ["Metoprolol"]


def test_retry_cannot_pad_the_list() -> None:
    fever = fact("Fieber", "present", "symptom", ev=((3, "Fieber"),))
    seen_limits = []

    class Spy(ScriptedLLM):
        def structured(self, name, system, user, schema, max_tokens=4000):
            if name == "retry":
                seen_limits.append(schema.model_json_schema()["properties"]["facts"]["maxItems"])
            return super().structured(name, system, user, schema, max_tokens)

    extract_facts(LINES, Spy([[fever], [fever]], {"Fieber": ["unsupported", "unsupported"]}))
    assert seen_limits == [1]


def test_failed_retry_keeps_verified_facts() -> None:
    from clinic_sidecar.agents.llm import LLMError

    class RetryFails(ScriptedLLM):
        def structured(self, name, system, user, schema, max_tokens=4000):
            if name == "retry":
                raise LLMError("cut off")
            return super().structured(name, system, user, schema, max_tokens)

    fever = fact("Fieber", "present", "symptom", ev=((3, "Fieber"), (4, "Nein")))
    llm = RetryFails([[fact("Ramipril"), fever]], {"Ramipril": ["supported"], "Fieber": ["wrong_assertion"]})
    result = extract_facts(LINES, llm)
    assert [f.name for f in result.facts] == ["Ramipril"]
    assert [r.fact.name for r in result.rejected] == ["Fieber"]
    assert any("retry 1 failed" in line for line in result.log)

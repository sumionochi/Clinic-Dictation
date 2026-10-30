import pytest

from clinic_sidecar.agents.graph import Contradiction
from clinic_sidecar.agents.schema import Evidence, Fact, TranscriptLine
from clinic_sidecar.fhir.mapper import DocumentInput, build_document
from clinic_sidecar.fhir.validator import validate, validator_available

LINES = [
    TranscriptLine(line=i, speaker=s, text=t)
    for i, (s, t) in enumerate(
        [
            ("Doctor", "Was führt Sie zu uns?"),
            ("Patient", "Brustschmerzen seit drei Tagen, kein Fieber."),
            ("Patient", "Ramipril 5 mg, Metoprolol abgesetzt, kein Apixaban."),
            ("Patient", "Gegen Penicillin allergisch, sonst keine Allergien, Latex nicht mehr."),
            ("Patient", "Blinddarm-OP 2010, Herzinfarkt hatte ich nicht, Bluthochdruck ja."),
            ("Patient", "Ehemaliger Raucher, Alkohol nie, lebe allein."),
        ],
        start=1,
    )
]


def fact(cat, name, assertion, line, quote, details=None) -> Fact:
    return Fact(category=cat, name=name, assertion=assertion, details=details, evidence=[Evidence(line=line, quote=quote)])


FACTS = [
    fact("complaint", "Brustschmerzen", "present", 2, "Brustschmerzen", "seit drei Tagen"),
    fact("symptom", "Fieber", "absent", 2, "kein Fieber"),
    fact("medication", "Ramipril", "present", 3, "Ramipril 5 mg", "5 mg"),
    fact("medication", "Metoprolol", "past", 3, "Metoprolol abgesetzt"),
    fact("medication", "Apixaban", "absent", 3, "kein Apixaban"),
    fact("allergy", "Penicillin", "present", 4, "Gegen Penicillin allergisch"),
    fact("allergy", "weitere Allergien", "absent", 4, "sonst keine Allergien"),
    fact("allergy", "Latex", "past", 4, "Latex nicht mehr"),
    fact("surgery", "Blinddarm-OP", "present", 5, "Blinddarm-OP 2010", "2010"),
    fact("condition", "Herzinfarkt", "absent", 5, "Herzinfarkt hatte ich nicht"),
    fact("condition", "Bluthochdruck", "present", 5, "Bluthochdruck ja"),
    fact("social", "Rauchen", "past", 6, "Ehemaliger Raucher"),
    fact("social", "Alkohol", "absent", 6, "Alkohol nie"),
]


def make(facts=FACTS, contradictions=()) -> dict:
    return build_document(
        DocumentInput(
            "session-1",
            list(facts),
            LINES,
            list(contradictions),
            started_at="2026-10-30T09:00:00+00:00",
            ended_at="2026-10-30T09:15:00+00:00",
            extraction_model="qwen3:8b",
            generated_at="2026-10-30T09:20:00+00:00",
        )
    )


def resources(bundle: dict, kind: str) -> list[dict]:
    return [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == kind]


def by_text(bundle: dict, kind: str, name: str) -> dict:
    for r in resources(bundle, kind):
        concept = r.get("code") or r.get("medicationCodeableConcept")
        if concept.get("text") == name:
            return r
    raise AssertionError(f"no {kind} {name}")


def test_document_shape() -> None:
    b = make()
    assert b["type"] == "document"
    assert b["entry"][0]["resource"]["resourceType"] == "Composition"
    assert b["entry"][0]["resource"]["status"] == "preliminary"
    assert len(resources(b, "Provenance")) == len(FACTS)


def test_every_reference_resolves_inside_the_bundle() -> None:
    b = make()
    full_urls = {e["fullUrl"] for e in b["entry"]}

    def refs(node):
        if isinstance(node, dict):
            if "reference" in node:
                yield node["reference"]
            for v in node.values():
                yield from refs(v)
        elif isinstance(node, list):
            for v in node:
                yield from refs(v)

    assert set(refs(b)) <= full_urls


@pytest.mark.parametrize(
    ("kind", "name", "path", "expected"),
    [
        ("Condition", "Fieber", ("verificationStatus",), "refuted"),
        ("Condition", "Bluthochdruck", ("verificationStatus",), "unconfirmed"),
        ("Condition", "Herzinfarkt", ("verificationStatus",), "refuted"),
        ("AllergyIntolerance", "Penicillin", ("clinicalStatus",), "active"),
        ("AllergyIntolerance", "Latex", ("clinicalStatus",), "resolved"),
    ],
)
def test_assertion_becomes_status(kind, name, path, expected) -> None:
    r = by_text(make(), kind, name)
    assert r[path[0]]["coding"][0]["code"] == expected


def test_medication_statuses() -> None:
    b = make()
    assert by_text(b, "MedicationStatement", "Ramipril")["status"] == "active"
    assert by_text(b, "MedicationStatement", "Ramipril")["dosage"][0]["text"] == "5 mg"
    assert by_text(b, "MedicationStatement", "Metoprolol")["status"] == "stopped"
    assert by_text(b, "MedicationStatement", "Apixaban")["status"] == "not-taken"


def test_no_further_allergies_is_snomed_no_known_allergy() -> None:
    r = by_text(make(), "AllergyIntolerance", "weitere Allergien")
    assert r["code"]["coding"][0]["code"] == "716186003"


def test_provenance_points_at_the_quote() -> None:
    b = make()
    target = by_text(b, "MedicationStatement", "Apixaban")
    prov = next(p for p in resources(b, "Provenance") if p["target"][0]["reference"] == f"urn:uuid:{target['id']}")
    assert prov["entity"][0]["what"]["display"] == 'Transkript Zeile 3 (Patient): "kein Apixaban"'
    assert prov["entity"][0]["what"]["identifier"]["value"] == "3"


def test_contradictions_get_their_own_section() -> None:
    b = make(contradictions=[Contradiction("medication", "Metoprolol", ["past", "present"], [3, 9])])
    titles = [s["title"] for s in b["entry"][0]["resource"]["section"]]
    assert "Widersprüche im Gespräch (bitte klären)" in titles


def test_ids_are_stable_between_exports() -> None:
    assert [e["fullUrl"] for e in make()["entry"]] == [e["fullUrl"] for e in make()["entry"]]


def test_narrative_is_escaped() -> None:
    b = make([fact("symptom", "<script>alert(1)</script>", "present", 2, "Brustschmerzen")])
    div = resources(b, "Condition")[0]["text"]["div"]
    assert "<script>" not in div and "&lt;script&gt;" in div


@pytest.mark.skipif(not validator_available(), reason="HL7 validator not installed")
def test_official_validator_reports_no_errors() -> None:
    report = validate(make(contradictions=[Contradiction("medication", "Metoprolol", ["past", "present"], [3])]))
    assert report.errors == [], report.errors

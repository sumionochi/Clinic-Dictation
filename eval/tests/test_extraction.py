from clinic_eval.extraction import (
    GoldFact,
    load_cases,
    names_match,
    score_contradictions,
    score_facts,
    score_verifier,
)

GOLD = [
    GoldFact("symptom", [("Brustschmerzen", "absent")]),
    GoldFact("medication", [("Ramipril", "present")]),
    GoldFact("social", [("Rauchen", "absent"), ("Nichtraucher", "present")]),
]


def f(category: str, name: str, assertion: str) -> dict:
    return {"category": category, "name": name, "assertion": assertion}


def test_names_match() -> None:
    assert names_match("Diabetes mellitus Typ 2", "Diabetes mellitus")
    assert names_match("ramipril", "Ramipril")
    assert not names_match("ASS", "Aspirin")  # synonyms must be listed in gold
    assert not names_match("Jod", "Jodid-Kontrastmittel allergie")  # too short to count as contained


def test_perfect_extraction() -> None:
    s = score_facts([f("complaint", "Brustschmerzen", "absent"), f("medication", "Ramipril", "present"),
                     f("social", "Nichtraucher", "present")], GOLD)
    assert (s.true_positives, s.false_positives, s.false_negatives) == (3, 0, 0)
    assert s.precision == s.recall == 1.0


def test_flipped_assertion_is_counted_as_dangerous() -> None:
    s = score_facts([f("symptom", "Brustschmerzen", "present")], GOLD)
    assert s.assertion_errors == 1
    assert s.false_positives == 1
    assert s.false_negatives == 3
    assert "wrong assertion" in s.wrong[0]


def test_invented_fact_is_a_false_positive() -> None:
    s = score_facts([f("condition", "Herzinfarkt", "present")], GOLD)
    assert (s.false_positives, s.assertion_errors) == (1, 0)


def test_repeated_fact_is_a_duplicate_not_an_error() -> None:
    s = score_facts([f("complaint", "Brustschmerzen", "absent"), f("symptom", "Brustschmerzen", "absent")], GOLD)
    assert (s.true_positives, s.false_positives, s.duplicates) == (1, 0, 1)


def test_contradictions() -> None:
    assert score_contradictions(["Metoprolol"], ["Metoprolol"]) == (1, 1)
    assert score_contradictions([], ["Metoprolol"]) == (0, 1)


def test_cases_load() -> None:
    cases = {c.name: c for c in load_cases()}
    assert {"allergy-meds", "history-social", "tricky-family-contradiction"} <= set(cases)
    tricky = cases["tricky-family-contradiction"]
    assert tricky.contradictions == ["Metoprolol"]
    assert tricky.lines[8][0] == "begleitung"


def test_verifier_score() -> None:
    kept = [f("medication", "Ramipril", "present"), f("condition", "Herzinfarkt", "present")]  # 1 right, 1 wrong
    rejected = [f("symptom", "Brustschmerzen", "present"), f("social", "Rauchen", "absent")]  # wrong, right
    v = score_verifier(kept, rejected, GOLD)
    assert (v.rejected, v.rejected_wrong, v.proposed_wrong) == (2, 1, 2)
    assert v.precision == 0.5  # one of its two rejections threw away a correct fact
    assert v.recall == 0.5  # it caught one of the two wrong facts


def test_roleplay_kit_is_well_formed() -> None:
    import json

    from clinic_eval.fixtures import EVAL_DIR

    script = EVAL_DIR / "scripts" / "roleplay-01-atemnot.tsv"
    rows = [ln.split("\t") for ln in script.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
    assert all(len(r) == 2 for r in rows)
    assert {r[0] for r in rows} == {"arzt", "patient", "begleitung"}
    gold = json.loads(script.with_name("roleplay-01-atemnot.gold.json").read_text(encoding="utf-8"))
    assert len(gold["facts"]) >= 20 and gold["contradictions"] == ["Torasemid"]

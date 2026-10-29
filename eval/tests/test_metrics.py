import pytest

from clinic_eval.metrics import score_case, score_speakers, summarize
from clinic_eval.normalize import normalize

TERMS = [normalize(t) for t in ["Penicillin", "Ramipril", "Metformin", "Milligramm", "Diabetes", "Diabetes mellitus", "L-Thyroxin"]]
NEGATIONS = ["kein", "keine", "nicht", "gegen"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ich nehme 5 Milligramm.", "ich nehme fünf milligramm"),
        ("2,5 mg", "zwei komma fünf milligramm"),
        ("Dr. Weber, 100 µg", "doktor weber hundert mikrogramm"),
        ("Neu-Delhi, Leibniz-Universität!", "neu delhi leibniz universität"),
        ("Ödeme  und  Übelkeit", "ödeme und übelkeit"),
        ("Straße", "straße"),
        ("L-Thyroxin 75", "l thyroxin fünfundsiebzig"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_perfect_transcript() -> None:
    s = score_case("c", "Ich nehme Ramipril.", "ich nehme ramipril", TERMS, NEGATIONS)
    assert s.wer == 0
    assert (s.terms.expected, s.terms.found) == (1, 1)


def test_digits_vs_words_are_not_errors() -> None:
    s = score_case("c", "Ramipril fünf Milligramm", "Ramipril 5 Milligramm", TERMS, NEGATIONS)
    assert s.wer == 0


def test_penicillin_inversion_is_caught() -> None:
    # The real failure seen in testing: an allergy turned into a medication.
    ref = "Gegen Penicillin bin ich allergisch."
    hyp = "Ich nehme Penicillin, Benichallergies."
    s = score_case("c", ref, hyp, TERMS, NEGATIONS)
    assert s.negations.expected == 1
    assert s.negations.found == 0
    assert s.negations.misses[0].term == "gegen"
    assert s.wer > 0.5


def test_term_found_elsewhere_still_counts_as_miss() -> None:
    # "Metformin" is misheard where it was said; a later correct mention must not hide that.
    ref = "Ich nehme Metformin. Kein Ramipril."
    hyp = "Ich nehme Medformin. Kein Ramipril. Metformin."
    s = score_case("c", ref, hyp, TERMS, NEGATIONS)
    assert (s.terms.expected, s.terms.found) == (2, 1)
    assert s.terms.misses[0].term == "metformin"
    assert s.terms.misses[0].heard == "medformin"


def test_dropped_negation() -> None:
    s = score_case("c", "Ich habe keine Allergien.", "Ich habe Allergien.", TERMS, NEGATIONS)
    assert (s.negations.expected, s.negations.found) == (1, 0)
    assert s.negations.misses[0].heard == ""
    assert s.deletions == 1


def test_phrase_counted_once() -> None:
    s = score_case("c", "Diabetes mellitus seit Jahren", "Diabetes mellitus seit Jahren", TERMS, NEGATIONS)
    assert s.terms.expected == 1


def test_multi_word_term_partially_wrong() -> None:
    s = score_case("c", "Ich nehme L-Thyroxin.", "Ich nehme El Thyroxin.", TERMS, NEGATIONS)
    assert (s.terms.expected, s.terms.found) == (1, 0)


def test_no_terms_means_no_recall() -> None:
    s = score_case("c", "Guten Tag.", "Guten Tag.", TERMS, NEGATIONS)
    assert s.terms.recall is None


def test_empty_reference_is_an_error() -> None:
    with pytest.raises(ValueError):
        score_case("c", "  ", "irgendwas", TERMS, NEGATIONS)


def test_summary_pools_words_not_cases() -> None:
    refs = ["eins zwei drei vier", "fünf"]
    hyps = ["eins zwei drei vier", "sechs"]
    scores = [score_case(str(i), r, h, TERMS, NEGATIONS) for i, (r, h) in enumerate(zip(refs, hyps))]
    summary = summarize(scores, refs, hyps)
    # Pooled: 1 error in 5 words = 0.2, not the average of 0.0 and 1.0.
    assert summary.wer == pytest.approx(0.2)
    assert summary.words == 5


def test_negation_form_change_is_not_a_lost_negation() -> None:
    s = score_case("c", "Ich habe keine Allergien.", "Ich habe kein Allergien.", TERMS, NEGATIONS)
    assert (s.negations.expected, s.negations.found) == (1, 1)
    assert s.wer > 0  # still a word error


def test_drug_names_are_not_interchangeable() -> None:
    s = score_case("c", "Ich nehme Ramipril.", "Ich nehme Metformin.", TERMS, NEGATIONS)
    assert (s.terms.expected, s.terms.found) == (1, 0)


def test_speakers_all_right() -> None:
    ref = [("arzt", "Haben Sie Allergien?"), ("patient", "Nein, keine.")]
    hyp = [("S1", "Haben Sie Allergien?"), ("S2", "Nein, keine.")]
    sc = score_speakers(ref, hyp)
    assert sc.accuracy == 1.0
    assert sc.mapping == {"S1": "arzt", "S2": "patient"}


def test_speakers_answer_given_to_doctor() -> None:
    ref = [("arzt", "Haben Sie Allergien?"), ("patient", "Nein keine")]
    hyp = [("S1", "Haben Sie Allergien? Nein keine")]
    sc = score_speakers(ref, hyp)
    assert sc.accuracy == pytest.approx(3 / 5)
    assert sc.voices_detected == 1


def test_one_person_split_into_two_voices_is_fine() -> None:
    ref = [("patient", "eins zwei drei vier")]
    hyp = [("S1", "eins zwei"), ("S2", "drei vier")]
    assert score_speakers(ref, hyp).accuracy == 1.0


def test_unlabelled_words_count_as_wrong() -> None:
    ref = [("arzt", "eins zwei")]
    hyp = [(None, "eins zwei")]
    assert score_speakers(ref, hyp).accuracy == 0.0

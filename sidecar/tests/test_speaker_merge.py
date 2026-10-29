from clinic_sidecar.diarizer import SpeakerTurn, renumber
from clinic_sidecar.speaker_merge import assign_speakers, split_by_speaker
from clinic_sidecar.transcriber import Segment, Word


def words(*spec: tuple[str, float, float]) -> list[Word]:
    return [Word(w, s, e, 0.9) for w, s, e in spec]


def test_segment_spanning_two_speakers_is_split() -> None:
    ws = words((" Haben", 0.0, 0.3), (" Sie", 0.3, 0.5), (" Allergien?", 0.5, 1.2), (" Nein.", 1.5, 1.9))
    seg = Segment(0.0, 1.9, "Haben Sie Allergien? Nein.", ws)
    turns = [SpeakerTurn(0.0, 1.3, "S1"), SpeakerTurn(1.4, 2.0, "S2")]
    out = split_by_speaker([seg], turns)
    assert [(s.text, spk) for s, spk in out] == [("Haben Sie Allergien?", "S1"), ("Nein.", "S2")]
    assert out[1][0].start == 1.5


def test_single_word_flip_is_smoothed() -> None:
    ws = words(("a", 0, 1), ("b", 1, 2), ("c", 2, 3))
    turns = [SpeakerTurn(0, 1, "S1"), SpeakerTurn(1, 2, "S2"), SpeakerTurn(2, 3, "S1")]
    assert assign_speakers(ws, turns) == ["S1", "S1", "S1"]


def test_word_in_a_gap_takes_nearest_or_previous_speaker() -> None:
    ws = words(("a", 0.0, 0.5), ("b", 0.62, 0.7), ("c", 5.0, 5.2))
    turns = [SpeakerTurn(0.0, 0.5, "S1")]
    # b: 160 ms after the turn, too far for 'nearest', but within the carry-over gap of
    # the previous word → S1. c: far from everything → unknown.
    assert assign_speakers(ws, turns) == ["S1", "S1", None]


def test_no_turns_leaves_speakers_unknown() -> None:
    seg = Segment(0, 1, "Hallo", words(("Hallo", 0, 1)))
    assert split_by_speaker([seg], []) == [(seg, None)]


def test_speakers_renamed_by_first_appearance() -> None:
    turns = [SpeakerTurn(2.0, 3.0, "0"), SpeakerTurn(0.0, 1.0, "3"), SpeakerTurn(4.0, 5.0, "3")]
    assert [(t.start, t.speaker) for t in renumber(turns)] == [(0.0, "S1"), (2.0, "S2"), (4.0, "S1")]


def test_first_word_before_any_turn_takes_next_speaker() -> None:
    ws = words(("Guten", 0.0, 0.16), ("Tag", 0.16, 0.5))
    turns = [SpeakerTurn(0.24, 4.0, "S1")]
    assert assign_speakers(ws, turns) == ["S1", "S1"]

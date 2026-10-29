# Adapted from TranscriptionSuite (https://github.com/homelab-00/TranscriptionSuite) —
# server/backend/core/speaker_merge.py: assigning each word to the speaker turn it
# overlaps most (with padding and fallbacks), then smoothing one-word speaker flips.
"""Combine Whisper's words with the diarizer's speaker turns.

Each word gets a speaker; then every Whisper segment is split wherever the
speaker changes, so "Haben Sie Allergien? Nein." said by two people becomes two
lines with two speakers.
"""

from dataclasses import replace

from clinic_sidecar.diarizer import SpeakerTurn
from clinic_sidecar.transcriber import Segment, Word

WORD_PADDING_S = 0.040  # boundary jitter between ASR and diarization timestamps
NEAREST_TURN_S = 0.120  # fallback: a turn this close to the word counts
CARRY_OVER_GAP_S = 0.200  # fallback: keep the previous word's speaker across short gaps


def _speaker_for(word: Word, turns: list[SpeakerTurn], previous: tuple[str, float] | None) -> str | None:
    mid = (word.start + word.end) / 2
    best, best_overlap = None, 0.0
    containing = nearest = None
    nearest_dist = None
    for t in turns:
        overlap = min(word.end + WORD_PADDING_S, t.end) - max(word.start - WORD_PADDING_S, t.start)
        if overlap > best_overlap:
            best, best_overlap = t.speaker, overlap
        if containing is None and t.start <= mid <= t.end:
            containing = t.speaker
        dist = t.start - mid if mid < t.start else mid - t.end if mid > t.end else 0.0
        if dist <= NEAREST_TURN_S and (nearest_dist is None or dist < nearest_dist):
            nearest, nearest_dist = t.speaker, dist
    if best is not None:
        return best
    if containing is not None:
        return containing
    if nearest is not None:
        return nearest
    if previous and word.start - previous[1] <= CARRY_OVER_GAP_S:
        return previous[0]
    return None


def assign_speakers(words: list[Word], turns: list[SpeakerTurn]) -> list[str | None]:
    speakers: list[str | None] = []
    previous: tuple[str, float] | None = None
    for w in words:
        s = _speaker_for(w, turns, previous)
        speakers.append(s)
        if s is not None:
            previous = (s, w.end)
    # Backward pass: a word just before a turn starts (often the very first word,
    # "Guten" in "Guten Tag") takes the speaker of the word right after it.
    for i in range(len(words) - 2, -1, -1):
        if speakers[i] is None and speakers[i + 1] is not None and words[i + 1].start - words[i].end <= CARRY_OVER_GAP_S:
            speakers[i] = speakers[i + 1]
    return _smooth(speakers)


def _smooth(speakers: list[str | None]) -> list[str | None]:
    """A single word whose speaker differs from identical neighbours on both sides
    is relabelled: "ja" flipping to the other speaker mid-sentence is noise."""
    out = list(speakers)
    for i in range(1, len(out) - 1):
        if out[i - 1] == out[i + 1] and out[i] != out[i - 1] and out[i - 1] is not None:
            out[i] = out[i - 1]
    return out


def split_by_speaker(segments: list[Segment], turns: list[SpeakerTurn]) -> list[tuple[Segment, str | None]]:
    """Each Whisper segment, split into runs of one speaker. Segments without word
    timings keep their place and get the speaker of their midpoint."""
    all_words = [w for s in segments for w in s.words]
    word_speakers = iter(assign_speakers(all_words, turns))
    out: list[tuple[Segment, str | None]] = []
    for seg in segments:
        if not seg.words:
            probe = Word("", seg.start, seg.end, 0.0)
            out.append((seg, _speaker_for(probe, turns, None)))
            continue
        runs: list[tuple[str | None, list[Word]]] = []
        for w in seg.words:
            spk = next(word_speakers)
            if runs and runs[-1][0] == spk:
                runs[-1][1].append(w)
            else:
                runs.append((spk, [w]))
        if len(runs) == 1:
            out.append((seg, runs[0][0]))
            continue
        for spk, words in runs:
            text = "".join(w.word for w in words).strip()
            out.append((replace(seg, start=words[0].start, end=words[-1].end, text=text, words=words), spk))
    return out

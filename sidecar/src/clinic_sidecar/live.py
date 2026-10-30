"""The live pass: a quick transcript while the conversation is still going.

Every ~20 s of new audio is transcribed on its own and appended to the live
transcript, which feeds the checklist. It is a preview: the final transcript is
made from the whole recording after it stops, with full context.

Windows end at the quietest moment of their last two seconds, so a cut rarely
falls in the middle of a word. `live_transcript` simulates exactly this on a
finished recording, which is how the evaluation measures live vs final accuracy.
"""

import numpy as np

from clinic_sidecar.transcriber import SAMPLE_RATE, Segment, Transcriber

WINDOW_S = 20.0
SEARCH_S = 2.0  # look this far back from the window end for a quiet point
FRAME_S = 0.1


def cut_point(audio: np.ndarray, start: int, end: int) -> int:
    """Sample index in [end - SEARCH_S, end] at the end of the quietest 100 ms frame."""
    frame = int(FRAME_S * SAMPLE_RATE)
    lo = max(start, end - int(SEARCH_S * SAMPLE_RATE))
    if end - lo < 2 * frame:
        return end
    best, best_energy = end, None
    for frame_end in range(lo + frame, end + 1, frame):
        chunk = audio[frame_end - frame : frame_end]
        energy = float(np.mean(chunk * chunk))
        if best_energy is None or energy < best_energy:
            best, best_energy = frame_end, energy
    return best


def next_window(audio: np.ndarray, start: int, final: bool = False) -> int | None:
    """End of the next live window starting at `start`, or None if there is not
    enough new audio yet. With `final`, whatever is left becomes the last window."""
    available = audio.size - start
    if available <= 0:
        return None
    if available < int(WINDOW_S * SAMPLE_RATE):
        return audio.size if final else None
    return cut_point(audio, start, start + int(WINDOW_S * SAMPLE_RATE))


def transcribe_window(
    transcriber: Transcriber, audio: np.ndarray, start: int, end: int, language: str | None
) -> list[Segment]:
    offset = start / SAMPLE_RATE
    segments = transcriber.transcribe(audio[start:end], language)
    out = []
    for s in segments:
        s.start += offset
        s.end += offset
        for w in s.words:
            w.start += offset
            w.end += offset
        out.append(s)
    return out


def live_transcript(transcriber: Transcriber, audio: np.ndarray, language: str | None) -> list[Segment]:
    """What the live pass would have produced for this recording, window by window."""
    segments: list[Segment] = []
    start = 0
    while (end := next_window(audio, start, final=True)) is not None:
        segments.extend(transcribe_window(transcriber, audio, start, end, language))
        start = end
    return segments

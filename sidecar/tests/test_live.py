import numpy as np

from clinic_sidecar.live import WINDOW_S, cut_point, live_transcript, next_window
from clinic_sidecar.transcriber import SAMPLE_RATE, Segment

SR = SAMPLE_RATE


def noise(seconds: float) -> np.ndarray:
    return np.random.default_rng(0).uniform(-0.5, 0.5, int(seconds * SR)).astype(np.float32)


def test_cut_lands_in_the_quiet_gap() -> None:
    audio = noise(25)
    audio[int(18.6 * SR) : int(18.9 * SR)] = 0  # a short pause 1.1-1.4 s before the 20 s mark
    end = cut_point(audio, 0, int(20 * SR))
    assert int(18.6 * SR) < end <= int(18.9 * SR)


def test_no_window_until_enough_audio() -> None:
    assert next_window(noise(5), 0) is None
    assert next_window(noise(5), 0, final=True) == 5 * SR
    assert next_window(noise(5), 5 * SR, final=True) is None


def test_full_window_ends_near_window_length() -> None:
    end = next_window(noise(30), 0)
    assert (WINDOW_S - 2) * SR <= end <= WINDOW_S * SR


class EchoTranscriber:
    """Returns one segment per call covering the slice, so offsets can be checked."""

    model_name = "echo"

    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio, language):
        self.calls += 1
        return [Segment(0.0, audio.size / SR, f"window {self.calls}")]


def test_live_transcript_covers_the_recording_in_windows() -> None:
    t = EchoTranscriber()
    segs = live_transcript(t, noise(45), "de")
    assert t.calls == 3
    assert segs[0].start == 0.0
    assert segs[1].start == segs[0].end  # windows follow each other with times shifted
    assert abs(segs[-1].end - 45) < 1e-6

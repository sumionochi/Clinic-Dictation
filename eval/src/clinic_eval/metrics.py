"""Speech recognition metrics for one consultation.

- WER and CER over the whole transcript (jiwer), after normalisation.
- Term recall: of the medical terms in the reference, how many Whisper got exactly
  right *at that position*. Uses jiwer's alignment, so a drug name that appears
  correctly somewhere else in the transcript does not hide a miss.
- Negation recall: the same for words like "kein" and "nicht", whose loss inverts
  a statement.
- Speaker accuracy: of the words recognised in the right place, how many are
  attributed to the right person.
"""

from dataclasses import dataclass, field

import jiwer

from clinic_eval.normalize import normalize


@dataclass
class TermMiss:
    term: str
    heard: str  # what Whisper produced in that place ("" if dropped)


@dataclass
class TermScore:
    expected: int = 0
    found: int = 0
    misses: list[TermMiss] = field(default_factory=list)

    @property
    def recall(self) -> float | None:
        return self.found / self.expected if self.expected else None


@dataclass
class CaseScore:
    case: str
    words: int
    wer: float
    cer: float
    substitutions: int
    deletions: int
    insertions: int
    terms: TermScore
    negations: TermScore


def _find_phrase(tokens: list[str], phrase: list[str]) -> list[int]:
    n = len(phrase)
    return [i for i in range(len(tokens) - n + 1) if tokens[i : i + n] == phrase]


def _score_terms(
    ref_tokens: list[str],
    hyp_tokens: list[str],
    alignment,
    terms: list[str],
    interchangeable: bool = False,
) -> TermScore:
    """With `interchangeable`, any listed term heard in place of another counts as
    found: "keine" heard as "kein" is a grammar slip, the negation survived."""
    term_set = set(terms)
    correct: set[int] = set()
    # For each reference index, the hypothesis words aligned to it (for reporting misses).
    heard_at: dict[int, str] = {}
    for chunk in alignment:
        hyp_text = " ".join(hyp_tokens[chunk.hyp_start_idx : chunk.hyp_end_idx])
        for offset, i in enumerate(range(chunk.ref_start_idx, chunk.ref_end_idx)):
            if chunk.type == "equal":
                correct.add(i)
            elif chunk.type == "substitute":
                # jiwer substitution chunks pair ref and hyp words one to one.
                same_slot = hyp_tokens[chunk.hyp_start_idx + offset]
                if interchangeable and same_slot in term_set and ref_tokens[i] in term_set:
                    correct.add(i)
            heard_at[i] = hyp_text if chunk.type == "substitute" else ""

    score = TermScore()
    # Longer phrases first, so "diabetes mellitus" is scored once rather than also as "diabetes".
    claimed: set[int] = set()
    for term in sorted(terms, key=lambda t: -len(t.split())):
        phrase = term.split()
        for start in _find_phrase(ref_tokens, phrase):
            span = range(start, start + len(phrase))
            if any(i in claimed for i in span):
                continue
            claimed.update(span)
            score.expected += 1
            if all(i in correct for i in span):
                score.found += 1
            else:
                heard = " ".join(dict.fromkeys(heard_at.get(i, "") for i in span if heard_at.get(i)))
                score.misses.append(TermMiss(term=term, heard=heard))
    return score


def score_case(case: str, reference: str, hypothesis: str, terms: list[str], negations: list[str]) -> CaseScore:
    ref = normalize(reference)
    hyp = normalize(hypothesis)
    if not ref:
        raise ValueError(f"{case}: reference transcript is empty")
    words = jiwer.process_words(ref, hyp)
    alignment = words.alignments[0]
    ref_tokens, hyp_tokens = ref.split(), hyp.split()
    return CaseScore(
        case=case,
        words=len(ref_tokens),
        wer=words.wer,
        cer=jiwer.cer(ref, hyp),
        substitutions=words.substitutions,
        deletions=words.deletions,
        insertions=words.insertions,
        terms=_score_terms(ref_tokens, hyp_tokens, alignment, terms),
        negations=_score_terms(ref_tokens, hyp_tokens, alignment, negations, interchangeable=True),
    )


@dataclass
class Summary:
    cases: int
    words: int
    wer: float
    cer: float
    term_recall: float | None
    negation_recall: float | None


def summarize(scores: list[CaseScore], references: list[str], hypotheses: list[str]) -> Summary:
    """Corpus-level numbers: WER over all words pooled, not an average of per-case WERs."""
    refs = [normalize(r) for r in references]
    hyps = [normalize(h) for h in hypotheses]

    def pooled(get) -> float | None:
        expected = sum(get(s).expected for s in scores)
        return sum(get(s).found for s in scores) / expected if expected else None

    return Summary(
        cases=len(scores),
        words=sum(s.words for s in scores),
        wer=jiwer.wer(refs, hyps),
        cer=jiwer.cer(refs, hyps),
        term_recall=pooled(lambda s: s.terms),
        negation_recall=pooled(lambda s: s.negations),
    )


@dataclass
class SpeakerScore:
    accuracy: float | None  # share of aligned words given to the right speaker
    words: int  # aligned words that were compared
    voices_detected: int
    speakers_in_reference: int
    mapping: dict[str, str]  # detected voice -> reference speaker


def score_speakers(
    reference_turns: list[tuple[str, str]], hypothesis_turns: list[tuple[str | None, str]]
) -> SpeakerScore:
    """Turns are (speaker, text). Each detected voice is mapped to the reference
    speaker it overlaps with most; several voices may map to one person, as the
    doctor can do in the app when the diarizer splits someone in two."""
    ref_tokens: list[tuple[str, str]] = [(spk, w) for spk, text in reference_turns for w in normalize(text).split()]
    hyp_tokens: list[tuple[str | None, str]] = [
        (spk, w) for spk, text in hypothesis_turns for w in normalize(text).split()
    ]
    voices = {spk for spk, _ in hyp_tokens if spk}
    ref_speakers = {spk for spk, _ in ref_tokens}
    if not ref_tokens or not hyp_tokens:
        return SpeakerScore(None, 0, len(voices), len(ref_speakers), {})
    out = jiwer.process_words(" ".join(w for _, w in ref_tokens), " ".join(w for _, w in hyp_tokens))
    pairs: list[tuple[str, str | None]] = []
    for chunk in out.alignments[0]:
        if chunk.type in ("equal", "substitute"):
            for k in range(chunk.ref_end_idx - chunk.ref_start_idx):
                pairs.append((ref_tokens[chunk.ref_start_idx + k][0], hyp_tokens[chunk.hyp_start_idx + k][0]))
    counts: dict[str, dict[str, int]] = {}
    for ref_spk, hyp_spk in pairs:
        if hyp_spk:
            counts.setdefault(hyp_spk, {}).setdefault(ref_spk, 0)
            counts[hyp_spk][ref_spk] += 1
    mapping = {voice: max(c, key=c.get) for voice, c in counts.items()}
    right = sum(1 for ref_spk, hyp_spk in pairs if hyp_spk and mapping.get(hyp_spk) == ref_spk)
    return SpeakerScore(
        accuracy=right / len(pairs) if pairs else None,
        words=len(pairs),
        voices_detected=len(voices),
        speakers_in_reference=len(ref_speakers),
        mapping=dict(sorted(mapping.items())),
    )

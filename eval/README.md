# Evaluation

Measures how accurately the app's speech recognition captures German
consultations. Milestone 1 reports these numbers on our own role-play recordings.

| Metric | What it tells us |
|---|---|
| **WER / CER** | Word and character error rate over the whole transcript, after normalisation (case, punctuation, `5` = `fünf`, `mg` = `Milligramm`). |
| **Term recall** | Of the medical terms in `terms/medical_de.txt` that were said, how many were recognised exactly, *at the place they were said*. |
| **Negation recall** | The same for `kein`, `nicht`, `gegen` and similar. Losing one inverts a statement, so it is a safety metric of its own. |
| **Speed** | Audio length ÷ transcription time on this machine (model already loaded). |

Every run is saved to `results/<date>-<model>.json` with both transcripts, so
numbers can be compared between models and over time.

## Run it

```bash
cd eval
uv sync
uv run clinic-eval synth      # render the synthetic smoke-test cases (macOS `say`)
uv run clinic-eval run        # transcribe every case and score it
```

`run` uses the model the app already downloaded and stays offline. Pass
`--model <hugging face repo>` to compare another MLX Whisper model (add
`--allow-download` if it is not on disk yet), and case names to run only some.

Tests: `uv run pytest`.

## Adding a role-play consultation

The synthetic cases only check that the pipeline works: a clear computer voice
reading a script is far easier than two people talking. The numbers that matter
come from role-plays.

1. **Write a script** in German (see the checklist below) and save it, e.g.
   `scripts/roleplay-03.md`.
2. **Record it in the app**: Consent → New consultation → everyone agrees →
   Session → Start recording. Speak naturally; small deviations from the script
   are fine.
3. **Import it**: `uv run clinic-eval add roleplay-03 --latest`
   (or `--audio some-file.m4a` for a recording made elsewhere).
4. **Write `fixtures/roleplay-03/reference.tsv`**: one turn per line,
   `speaker<TAB>text`, from the script, corrected by listening for anything said
   differently. Never paste the app's transcript in: a reference made from the
   model's output hides exactly the errors we want to count.
5. **Run** `uv run clinic-eval run`.

The audio stays local (`*.wav` is gitignored); only `reference.tsv` is committed.
Only use voices of people who agreed to it, and no real patient data.

### Script checklist

Each script should be an 8–15 minute internal medicine admission interview and
include most of these, because they are where transcription and extraction fail:

- [ ] Chief complaint with duration and severity ("seit drei Tagen", "Schmerz 7 von 10")
- [ ] Two or more medications with doses and timing (Ramipril 5 mg morgens)
- [ ] An allergy *and* an explicit "keine weiteren Allergien"
- [ ] Negated symptoms ("kein Fieber", "nicht geraucht")
- [ ] A number said as digits-worthy value (blood pressure, weight, Packungsjahre)
- [ ] Previous illnesses and operations
- [ ] Social situation (lives alone, Pflegedienst, Pflegegrad)
- [ ] One contradiction (stopped a drug, later says still taking it)
- [ ] A companion or interpreter speaking at least once
- [ ] Some interruptions, overlapping speech or background noise

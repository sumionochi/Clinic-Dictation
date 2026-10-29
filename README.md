# Clinic Dictation

Local-first, consent-gated capture of doctor and patient conversations into
evidence-linked, FHIR-structured clinical documentation.

Course project for Frontier Systems 2026/27 (L3S, Leibniz Universität Hannover).

Everything runs on one machine. Audio and transcripts never leave it. The app
only structures what was said: every field links back to the words in the
transcript that support it, or stays empty. It never suggests diagnoses, and
nothing enters the record without the doctor's approval.

## Layout

```
app/        Electron + React + shadcn/ui desktop app
sidecar/    Python FastAPI service for ASR, agents and FHIR (from step 2)
eval/       Gold forms and evaluation scripts (from step 5)
```

## Run the app

Requires Node 22.12 or newer (see `.nvmrc`) and [uv](https://docs.astral.sh/uv/)
for the Python sidecar (Python 3.13).

```bash
cd sidecar && uv sync && cd ..
cd app
nvm use
npm install
npm run dev
```

The app starts the sidecar itself on `127.0.0.1` with a random port and a
per-launch token, and stops it on quit. If the app is killed, the sidecar
notices within two seconds and exits on its own.

Sidecar tests: `cd sidecar && uv run pytest`.

Local data lives in `~/Library/Application Support/clinic-dictation/data/`
(`clinic.sqlite`). The consent log in it is append-only: a change of mind is a
new entry, never an edit.

## License and credits

GPL-3.0-or-later, see [LICENSE](LICENSE).

Parts of this project are adapted from
[TranscriptionSuite](https://github.com/homelab-00/TranscriptionSuite)
(GPL-3.0-or-later). Every adapted file carries a comment naming its source.

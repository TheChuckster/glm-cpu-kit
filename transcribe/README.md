# transcribe — GPU speech-to-text with speaker attribution

Local transcription on the RTX 5090, with automatic speaker labels. Nothing is
uploaded, and no HuggingFace token is required.

```bash
./transcribe/setup.sh                              # once
./transcribe.sh recording.m4a                      # auto-detect speaker count
./transcribe.sh call.mp3 --speakers 2
./transcribe.sh mtg.wav --names "Alice,Bob,Carol"  # first-heard order
```

Writes `.txt` (readable, grouped by speaker turn), `.srt` (subtitles with
`[SPEAKER]` prefixes) and `.json` (per-word timestamps and confidences).

## What it uses

| | |
|---|---|
| ASR | `faster-whisper` large-v3 (CTranslate2) on CUDA |
| Diarization | SpeechBrain ECAPA-TDNN embeddings + agglomerative clustering |

Both models are **ungated**. That constraint is the whole reason the diarizer is
hand-rolled rather than pyannote — see below.

## Accuracy — measured, not claimed

**92.4% segment-level speaker accuracy** (73 of 79 segments) on a 2-speaker 8:56
telephone call, scored against a hand-labelled reference transcript.

That is measured on the tool's real output, not on the clustering in isolation.
(It read 87.8% while this used faster-whisper's VAD; replacing that with
`no_speech_prob` filtering both fixed a segment-loss bug and tightened segment
boundaries, which helps the labels line up.)

Telephone audio is the hard case: band-limited, single channel, and both voices
carry the same channel colouration. Clean wideband audio with distinct voices
does better. **The words themselves are Whisper large-v3 quality** — it's the
speaker labels that are ~88%, not the transcription.

Not a certified transcript. Verify any quotation against the audio before
relying on it, and re-check every speaker change before quoting across one.

## Speed on the 5090

ASR runs **34–51x realtime**. An 8-minute call is ~10 seconds of ASR; a 2-hour
recording is ~4 minutes. Diarization adds roughly 20% on top, plus a one-off
~80s the first time it fetches the ECAPA model.

## Two findings worth keeping

**`wav_lens` is required, not optional.** SpeechBrain's `encode_batch` takes a
relative-length tensor. Omit it and ECAPA treats zero padding as signal, so a
short window's embedding depends on which other windows share its batch. Measured
as a 31-point accuracy swing (62.2% → 93.3% at win=3.0/hop=1.5), and it presents
as nondeterminism — the same config scoring differently on different runs.

**Mean-normalisation is doing real work.** Subtracting the global embedding mean
before clustering is channel compensation. Without it, accuracy drops from 90% to
58–62%, because both telephone voices share a strong band-limited component that
dominates the cosine similarity.

## Clustering configuration

Measured against the reference call (clustering-only accuracy):

```
win=2.0s hop=1.0s  complete linkage   91.1%   <- default
win=1.5s hop=0.75s complete linkage   90.0%
win=2.0s hop=1.0s  average  linkage   90.0%
win=2.5s hop=1.0s  average  linkage   62.2%   <- average collapses
win=3.0s hop=0.75s either             62.2%   <- both collapse
```

62.2% is the majority-class baseline — clustering failed and assigned nearly
everything to one speaker. `complete` linkage failed once in six configurations,
`average` three times, so complete is the default.

**If you change `--win`/`--hop`, re-measure.** The failure mode is silent: you
get a perfectly plausible transcript with the speaker labels smeared across both
people, which is worse than an obvious error because nothing flags it.

When auto-detecting speaker count, the silhouette scores for each candidate `k`
are written into the `.txt` header and the `.json`. A low best score means the
speakers aren't cleanly separable and the labels are guesswork — check it.

## Why not pyannote

`pyannote/speaker-diarization-3.1` is the usual answer and is better at
overlapped speech. Every pyannote pipeline on the Hub is **gated** (verified via
the HF API: `3.1`, `segmentation-3.0` and `community-1` are all `gated: auto`),
so it can't run until you accept terms on huggingface.co and mint a token.

There is **no pyannote code path in this tool**. Setting `HF_TOKEN` does nothing
here. Adding one would mean: `uv pip install pyannote.audio`, accepting the terms
for both `speaker-diarization-3.1` and `segmentation-3.0`, then replacing
`embed_windows` + `cluster` with a `Pipeline.from_pretrained(...)` call and
mapping its output onto the ASR segments the way `assign()` already does.

NVIDIA's Sortformer (`nvidia/diar_sortformer_4spk-v1`) is ungated and would
likely beat this, but `nemo_toolkit[asr]` does not install on Python 3.12 here —
its `librosa` pin drags in `numba 0.53.1` → `llvmlite 0.36`, which fails to
build. Worth revisiting if that resolves.

## Environment notes

- **Python 3.12 is pinned** in the venv. System python is 3.14, which most of the
  ML stack has no wheels for.
- **torch cu128 on a CUDA 13.3 driver** is correct — the driver is backward
  compatible, and cu128 is the wheel series carrying Blackwell `sm_120` kernels.
  Verified: `torch 2.11.0+cu128`, `sm_120`, GPU matmul works.
- Earlier work on this box concluded the 5090 was unusable ("NVML has a
  driver/library mismatch, so CPU it is") and transcribed on CPU. **That is no
  longer true** as of driver 610.43.03 — the GPU works.
- The ASR model is reused from `~/.cache/whisper-large-v3` if present, rather
  than re-downloading. This network's IPv6 route to the HF CDN hangs; if a
  download stalls, force IPv4.

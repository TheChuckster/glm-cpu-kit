#!/usr/bin/env bash
# Transcribe audio with speaker attribution, locally on the RTX 5090.
#
#   ./transcribe.sh recording.m4a                    # auto-detect speaker count
#   ./transcribe.sh call.mp3 --speakers 2            # force 2 speakers
#   ./transcribe.sh mtg.wav --names "Alice,Bob,Carol"  # name them, first-heard order
#   ./transcribe.sh long.m4a -o ~/out/meeting        # choose output basename
#   ./transcribe.sh x.mp4 --no-diarize               # transcript only
#
# Writes three files next to the input (or at -o): .txt (readable, grouped by
# speaker turn), .srt (subtitles with [SPEAKER] prefixes), .json (full detail
# including per-word timestamps and confidences).
#
# Accepts anything ffmpeg can read, including video - audio is extracted and
# downmixed to 16 kHz mono first, which is what both models expect.
#
# WHAT IT USES, and why it is all local:
#   ASR         faster-whisper large-v3 (CTranslate2) on CUDA
#   Diarization SpeechBrain ECAPA-TDNN embeddings + agglomerative clustering
# Nothing is uploaded and no HuggingFace token is needed - both models are
# ungated. pyannote is the better-known diarizer and slightly better on
# overlapped speech, but every pyannote pipeline on the Hub is gated, so it
# cannot work out of the box. See transcribe/README.md to opt into it.
#
# SPEED on the 5090: ASR runs ~34x realtime, so an 8-minute call is ~15s and a
# 2-hour recording is ~4 minutes. Diarization adds roughly 20% on top.
#
# ACCURACY, measured rather than claimed: 92.4% segment-level speaker accuracy
# end to end (73 of 79 segments) on a 2-speaker telephone call, scored against a
# hand-labelled reference. Telephone audio is the hard case; clean wideband audio does
# better. The words themselves are Whisper large-v3 quality. This is NOT a
# certified transcript - check any quotation against the audio before relying on
# it, especially if it is going into a filing.
#
# FIRST RUN downloads the ECAPA model (~80 MB) to ~/.cache/ecapa-voxceleb and,
# if large-v3 is not already at ~/.cache/whisper-large-v3, the ASR model (~3 GB).
# Run ./transcribe/setup.sh once first to create the venv.
set -euo pipefail
# Resolve our own location WITHOUT cd-ing: the arguments are the user's paths and
# must keep resolving against the user's cwd. `cd "$(dirname "$0")"` made the
# documented invocation - `cd ~/audio && /path/to/transcribe.sh recording.m4a` -
# fail with "no such file", or worse, silently transcribe a same-named file from
# the repo directory and write -o output there while printing it as if it were
# the user's.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VENV="$HERE/transcribe/.venv"
[ -x "$VENV/bin/python" ] || {
  echo "no venv at $VENV - run $HERE/transcribe/setup.sh first" >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "ffmpeg not found (pacman -S ffmpeg)" >&2; exit 1; }

exec "$VENV/bin/python" "$HERE/transcribe/transcribe.py" "$@"

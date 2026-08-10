#!/usr/bin/env python3
"""GPU transcription with speaker attribution.

faster-whisper (CTranslate2) for ASR, SpeechBrain ECAPA embeddings + agglomerative
clustering for diarization. Everything runs locally on the GPU; no audio and no
text leaves the machine, and no HuggingFace token is required - every model used
here is ungated.

Why not pyannote: pyannote/speaker-diarization-3.1 is the usual answer and is
better at overlapped speech, but every pyannote pipeline on the Hub is gated,
which means it cannot run until you accept terms on huggingface.co and mint a
token. This tool deliberately does not depend on that. There is NO pyannote code
path here - README.md sketches what adding one would take, but nothing in this
file will use pyannote if you set a token.

MEASURED ACCURACY, so you know what to trust: 92.4% segment-level speaker
accuracy end to end (73/79 segments) on a 2-speaker 8:56 telephone call, scored
against a hand-labelled reference. That is the number this tool actually
delivers, measured on its real output rather than on the clustering in
isolation. (It was 87.8% when this used faster-whisper's VAD; replacing that
with no_speech_prob filtering, for the reasons in transcribe() below, both fixed
a segment-loss bug and tightened segment boundaries, which helps the speaker
labels line up.) Telephone audio is the hard case (band-limited,
one channel); clean wideband audio with distinct voices does better. It is NOT a
certified transcript - verify any quotation against the audio before relying
on it.

The clustering settings are not arbitrary. Measured against that reference.
These are clustering-only numbers - scored on the window labels rather than on
the finished transcript - so they are not directly comparable to the 92.4%
end-to-end figure above:

    win=2.0s hop=1.0s  complete linkage   91.1%   <- default
    win=1.5s hop=0.75s complete linkage   90.0%
    win=2.0s hop=1.0s  average  linkage   90.0%
    win=2.5s hop=1.0s  average  linkage   62.2%   <- average collapses here
    win=3.0s hop=0.75s either             62.2%   <- both collapse here

62.2% is the majority-class baseline, i.e. clustering failed and put nearly
everything in one speaker. `complete` linkage failed once in six configurations,
`average` failed three times, which is why complete is the default. If you change
WIN/HOP, re-measure - the failure is silent and looks like a plausible transcript
with the speaker labels smeared.
"""
import argparse, json, os, subprocess, sys, tempfile, time
from pathlib import Path

import numpy as np


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def to_wav16k(src: Path, dst: Path):
    """Decode anything ffmpeg understands to 16 kHz mono float wav.

    16 kHz mono is what both Whisper and ECAPA want; handing them a 48 kHz stereo
    m4a means each library resamples separately and the diarization windows no
    longer line up sample-for-sample with the ASR timestamps.
    """
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(src), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)],
        check=True,
    )


def transcribe(wav: Path, model_path: str, device: str, compute_type: str,
               language: str | None, beam: int,
               no_speech_max: float = 0.6, logprob_min: float = -1.0):
    from faster_whisper import WhisperModel

    t0 = time.time()
    model = WhisperModel(model_path, device=device, compute_type=compute_type)
    log(f"ASR model loaded in {time.time()-t0:.1f}s ({model_path}, {device}/{compute_type})")

    t1 = time.time()
    # vad_filter is deliberately OFF, and condition_on_previous_text with it.
    #
    # Both defaults fail badly on a recording that opens with several minutes of
    # room tone. Measured on a 2h04m file whose speech starts at 5:30: with the
    # VAD on, Whisper emitted ONE segment spanning [0.4 - 365.7] and transcribed
    # only its tail, silently losing the first real exchange. Lowering the VAD
    # threshold, padding, and capping max_speech_duration_s all failed to
    # recover it; turning the VAD off did.
    #
    # The VAD's job - not transcribing noise - is done instead by no_speech_prob
    # below, which is a better instrument for it: on that file every room-tone
    # hallucination ("Thank you.", "I don't know.") scored 0.80-0.95 while every
    # real segment scored 0.18-0.56. Note avg_logprob does NOT separate them (a
    # hallucination at -0.47 beat real speech at -0.72), so it is only a loose
    # guard here, not the discriminator.
    segments, info = model.transcribe(
        str(wav), beam_size=beam, vad_filter=False, word_timestamps=True,
        condition_on_previous_text=False, language=language,
    )
    segs, dropped = [], 0
    for s in segments:                       # generator - consuming it does the work
        if s.no_speech_prob > no_speech_max or s.avg_logprob < logprob_min:
            dropped += 1
            continue
        segs.append({
            "start": s.start, "end": s.end, "text": s.text,
            "no_speech_prob": s.no_speech_prob, "avg_logprob": s.avg_logprob,
            "words": [{"word": w.word, "start": w.start, "end": w.end,
                       "prob": w.probability} for w in (s.words or [])],
        })
        if len(segs) % 100 == 0:
            log(f"  ...{len(segs)} segments, {s.end/60:.1f} min of audio")
    dur = segs[-1]["end"] if segs else 0.0
    el = time.time() - t1
    log(f"ASR done: {len(segs)} segments ({dropped} dropped as non-speech), "
        f"{dur/60:.1f} min audio in {el:.1f}s "
        f"({dur/max(el,1e-9):.1f}x realtime), language={info.language}")
    return segs, info


def embed_windows(wav: Path, segs, device: str, win: float, hop: float, batch: int):
    """ECAPA embedding per fixed-length window inside detected speech."""
    import soundfile as sf
    import torch
    from speechbrain.inference.speaker import EncoderClassifier

    audio, sr = sf.read(str(wav), dtype="float32")
    wins = []
    for s in segs:
        t = s["start"]
        while t + win <= s["end"] + 1e-6:
            wins.append((t, t + win))
            t += hop
        # keep the tail of a segment that the stride skipped, provided it is long
        # enough to embed at all - below ~0.6s ECAPA output is mostly noise
        if s["end"] - s["start"] >= 0.6 and (not wins or wins[-1][1] < s["end"] - 0.15):
            wins.append((max(s["start"], s["end"] - win), s["end"]))
    if not wins:
        return np.zeros((0, 192)), []
    log(f"diarization: {len(wins)} windows ({win}s / {hop}s hop)")

    enc = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=os.path.expanduser("~/.cache/ecapa-voxceleb"),
        run_opts={"device": device},
    )
    # Drop degenerate slices. faster-whisper never clamps a segment end to the
    # actual wav length, so a zero-length slice is reachable on real audio - and
    # one NaN row survives mean-normalisation into EVERY row, killing clustering
    # with "Input X contains NaN" after the whole ASR pass.
    kept = []
    for a, b in wins:
        i0, i1 = int(a*sr), int(b*sr)
        if i1 - i0 >= int(0.1*sr):
            kept.append((a, b))
    if len(kept) != len(wins):
        log(f"  dropped {len(wins)-len(kept)} degenerate windows (past end of audio)")
    wins = kept
    if not wins:
        return np.zeros((0, 192)), []
    chunks, out = [torch.from_numpy(audio[int(a*sr):int(b*sr)]) for a, b in wins], []
    for i in range(0, len(chunks), batch):
        c = chunks[i:i+batch]
        L = max(len(x) for x in c)
        bt = torch.zeros(len(c), L)
        for j, x in enumerate(c):
            bt[j, :len(x)] = x
        # wav_lens is REQUIRED, not optional. Without it ECAPA treats the zero
        # padding as signal, so a short window's embedding depends on which other
        # windows happen to share its batch. That measured as a 31-point accuracy
        # swing (62.2% -> 93.3% at win=3.0/hop=1.5) and looks like nondeterminism.
        lens = torch.tensor([len(x)/L for x in c])
        with torch.no_grad():
            e = enc.encode_batch(bt.to(device), lens.to(device))
        out.append(e.squeeze(1).cpu().numpy())
        if (i // batch) % 20 == 0 and i:
            log(f"  ...embedded {i}/{len(chunks)}")
    return np.vstack(out), wins


def cluster(W: np.ndarray, n_speakers: int | None, max_speakers: int, linkage: str):
    """Mean-normalise, then agglomerative-cluster on cosine distance.

    The mean subtraction is channel compensation and it is doing real work: on the
    reference call, skipping it drops accuracy from 90% to 58-62% because both
    telephone voices share a strong band-limited channel component that dominates
    the cosine similarity.
    """
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics import silhouette_score

    Wc = W - W.mean(0, keepdims=True)
    Wc /= np.maximum(np.linalg.norm(Wc, axis=1, keepdims=True), 1e-12)

    # sklearn refuses ward with a non-euclidean metric. The vectors are L2
    # normalised here, so euclidean and cosine induce the same ordering
    # (|a-b|^2 == 2-2cos) and ward stays meaningful instead of raising.
    metric = "euclidean" if linkage == "ward" else "cosine"

    def fit(k):
        return AgglomerativeClustering(n_clusters=k, metric=metric,
                                       linkage=linkage).fit_predict(Wc)

    if n_speakers:
        # Cap at the number of windows: sklearn raises "Cannot extract more
        # clusters than samples", and it does so AFTER the full ASR pass.
        k = min(n_speakers, len(Wc))
        if k < n_speakers:
            log(f"  --speakers {n_speakers} exceeds the {len(Wc)} diarization "
                f"windows available; using {k}")
        lab = fit(k)
        return lab, k, None

    # Auto: pick k by silhouette. Reported alongside the result rather than hidden,
    # because a low best-score means the speakers are not actually separable and
    # the labels below are guesswork.
    scores, labs = {}, {}
    for k in range(2, min(max_speakers, len(Wc) - 1) + 1):
        lab = fit(k)
        if len(set(lab)) < 2:
            continue
        labs[k] = lab
        scores[k] = silhouette_score(Wc, lab, metric="cosine")
    if not scores:
        return np.zeros(len(Wc), dtype=int), 1, None
    best = max(scores, key=scores.get)
    return labs[best], best, scores   # reuse the fit we already paid for


def assign(segs, wins, lab):
    """Majority vote of the windows overlapping each ASR segment."""
    starts = np.array([w[0] for w in wins])
    ends = np.array([w[1] for w in wins])
    for s in segs:
        m = (ends > s["start"]) & (starts < s["end"])
        v = lab[m]
        s["speaker"] = int(np.bincount(v).argmax()) if len(v) else None
    # A segment too short to contain a window inherits its predecessor rather than
    # showing as unknown; short backchannels ("mm-hm", "right") are the usual case.
    last = None
    for s in segs:
        if s["speaker"] is None:
            s["speaker"] = last
        else:
            last = s["speaker"]
    return segs


def ts(t: float) -> str:
    h, r = divmod(float(t), 3600)
    m, s = divmod(r, 60)
    return f"{int(h):d}:{int(m):02d}:{int(s):02d}" if h else f"{int(m):d}:{int(s):02d}"


def srt_ts(t: float) -> str:
    h, r = divmod(float(t), 3600)
    m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s%1)*1000):03d}"


def name_of(spk, names):
    if spk is None:
        return "UNKNOWN"
    return names[spk] if names and spk < len(names) else f"SPEAKER_{spk:02d}"


def write_outputs(segs, out_base: Path, src: Path, info, n_spk, names, sil, meta):
    out_base.parent.mkdir(parents=True, exist_ok=True)

    # NOT with_suffix: it strips the last dot-component, so
    # "call.2026-08-03" would write to call.txt and the next day's recording
    # would overwrite it without a word.
    def sibling(ext):
        return Path(str(out_base) + ext)

    txt = sibling(".txt")
    with txt.open("w") as f:
        f.write(f"TRANSCRIPT: {src.name}\n")
        f.write(f"Duration: {ts(segs[-1]['end']) if segs else '0:00'}  "
                f"Language: {info.language}  Speakers detected: {n_spk}\n")
        f.write(f"{meta}\n")
        f.write("Machine transcription with automatic speaker attribution. NOT a\n"
                "certified transcript, and speaker labels measured 92.4% accurate\n"
                "on 2-speaker telephone audio - verify any quotation against the\n"
                "audio, and re-check every speaker change before quoting it.\n")
        if sil:
            f.write(f"Speaker-count silhouette scores: "
                    f"{', '.join(f'{k}:{v:.3f}' for k,v in sorted(sil.items()))}\n")
        f.write("=" * 78 + "\n\n")
        prev = object()
        for s in segs:
            nm = name_of(s["speaker"], names)
            if nm != prev:
                f.write(f"\n{nm}:\n")
                prev = nm
            f.write(f"  [{ts(s['start'])} - {ts(s['end'])}] {s['text'].strip()}\n")

    srt = sibling(".srt")
    with srt.open("w") as f:
        for i, s in enumerate(segs, 1):
            f.write(f"{i}\n{srt_ts(s['start'])} --> {srt_ts(s['end'])}\n"
                    f"[{name_of(s['speaker'], names)}] {s['text'].strip()}\n\n")

    js = sibling(".json")
    json.dump({"source": str(src), "language": info.language, "speakers": n_spk,
               "silhouette": sil, "meta": meta,
               "segments": [{**s, "speaker_name": name_of(s["speaker"], names)}
                            for s in segs]},
              js.open("w"), indent=1)
    return txt, srt, js


def main():
    p = argparse.ArgumentParser(
        description="GPU transcription with speaker attribution (local, no HF token).")
    p.add_argument("audio", type=Path, help="any audio/video file ffmpeg can read")
    p.add_argument("-o", "--out", type=Path, help="output basename (default: alongside input)")
    p.add_argument("-s", "--speakers", type=int, help="exact speaker count (default: auto)")
    p.add_argument("--max-speakers", type=int, default=6, help="ceiling when auto-detecting")
    p.add_argument("--names", help="comma-separated speaker names, in first-heard order")
    p.add_argument("--model", default=os.environ.get("WHISPER_MODEL", "large-v3"),
                   help="faster-whisper model name or local CTranslate2 dir")
    p.add_argument("--language", default=None, help="force language (default: detect)")
    p.add_argument("--beam", type=int, default=5)
    p.add_argument("--device", default="cuda")
    p.add_argument("--compute-type", default="float16")
    p.add_argument("--win", type=float, default=2.0, help="diarization window seconds")
    p.add_argument("--hop", type=float, default=1.0, help="diarization hop seconds")
    p.add_argument("--linkage", default="complete", choices=["complete", "average", "ward"],
                   help="complete is the measured-robust default; see module docstring")
    p.add_argument("--batch", type=int, default=64)
    p.add_argument("--no-diarize", action="store_true", help="transcript only, no speakers")
    p.add_argument("--no-speech-max", type=float, default=0.6,
                   help="drop segments whose no_speech_prob exceeds this (hallucination filter)")
    p.add_argument("--logprob-min", type=float, default=-1.0,
                   help="drop segments below this avg_logprob (loose guard)")
    args = p.parse_args()

    if not args.audio.exists():
        sys.exit(f"no such file: {args.audio}")
    # --hop 0 never advances the window loop and grows `wins` until the OOM
    # killer fires; --win 0 makes every slice empty and divides by zero. Both
    # only surface after the full ASR pass, so check before spending it.
    if args.win <= 0 or args.hop <= 0:
        sys.exit(f"--win and --hop must be > 0 (got win={args.win} hop={args.hop})")
    if args.speakers is not None and args.speakers < 1:
        sys.exit(f"--speakers must be >= 1 (got {args.speakers})")

    # A local CTranslate2 directory beats re-downloading; this box already has
    # large-v3 there from earlier work, and HF over IPv6 hangs on this network.
    model = args.model
    local = Path(os.path.expanduser(f"~/.cache/whisper-{args.model}"))
    if local.is_dir() and (local / "model.bin").exists():
        model = str(local)

    # Path.with_suffix("") would turn "call.2026-08-03.m4a" into
    # "call.2026-08-03" -> fine - but "interview.v2.mp3" into "interview.v2",
    # and then sibling() keeps it whole. Strip only a real trailing extension.
    out_base = args.out or args.audio.parent / args.audio.name.rsplit(".", 1)[0]
    t_all = time.time()

    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "audio16k.wav"
        log(f"decoding {args.audio.name} -> 16 kHz mono")
        to_wav16k(args.audio, wav)

        segs, info = transcribe(wav, model, args.device, args.compute_type,
                                args.language, args.beam,
                                args.no_speech_max, args.logprob_min)
        if not segs:
            sys.exit("no speech found")

        n_spk, names, sil = 1, None, None
        # Default every segment to "no speaker" up front. Both places that used to
        # set this lived under `if not args.no_diarize`, so the documented
        # --no-diarize mode ran the whole ASR pass and then died with
        # KeyError: 'speaker' in write_outputs, producing no files at all.
        for s_ in segs:
            s_["speaker"] = None
        if not args.no_diarize:
            W, wins = embed_windows(wav, segs, args.device, args.win, args.hop, args.batch)
            if len(W) >= 2:
                lab, n_spk, sil = cluster(W, args.speakers, args.max_speakers, args.linkage)
                log(f"diarization: {n_spk} speakers"
                    + (f" (silhouette {sil[n_spk]:.3f})" if sil and n_spk in sil else ""))
                segs = assign(segs, wins, lab)
                # Renumber so SPEAKER_00 is whoever talks first - stable and readable.
                order, seen = {}, 0
                for s in segs:
                    if s["speaker"] is not None and s["speaker"] not in order:
                        order[s["speaker"]] = seen
                        seen += 1
                for s in segs:
                    if s["speaker"] is not None:
                        s["speaker"] = order[s["speaker"]]
                # Report what was actually LABELLED, not what clustering asked
                # for. A cluster can win windows yet never win a segment
                # majority, which had the header claiming "Speakers detected: 3"
                # over a transcript containing only SPEAKER_00 and SPEAKER_01
                # (and a --names entry that never appeared).
                if seen:
                    n_spk = seen
                if args.names:
                    names = [n.strip() for n in args.names.split(",")]
            else:
                log("too little speech to diarize; writing transcript without speakers")
                for s in segs:
                    s["speaker"] = None

    meta = (f"faster-whisper {Path(model).name} on {args.device}/{args.compute_type}; "
            f"diarization ECAPA win={args.win}s hop={args.hop}s linkage={args.linkage}")
    txt, srt, js = write_outputs(segs, out_base, args.audio, info, n_spk, names, sil, meta)
    log(f"total {time.time()-t_all:.1f}s")
    print(f"{txt}\n{srt}\n{js}")


if __name__ == "__main__":
    main()

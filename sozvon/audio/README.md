# Audio worker API

`sozvon.audio.operations.run(operation, payload, stop_event, emit) -> dict`

- `stop_event`: `threading.Event`; `emit`: callback accepting a JSON-serializable dictionary.
- `devices`, `audio_info`, `transcribe`, `record` follow `docs/CONTRACT.md`.
- All imports are native-library-free until an operation needs the relevant library.
- Device identifiers returned by discovery are opaque `mic:<sounddevice-index>` and
  `loop:<PyAudioWPatch-index>`. Null/empty device means the OS default, never the first device.
- `devices.available` means both capture source types were discovered. A single source can
  still be used only with `record.allow_partial=true`; `reason` explains unavailable sources.

## Capture

`CaptureService(payload, *, backends=None, queue_size=128)`:
`start() -> self`, `snapshot() -> progress_dict`, `stop() -> result_dict`.
The service is one-shot; `stop()` is idempotent after success. Each WAV uses the rate/channel
count validated by its own backend. Sounddevice microphone and PyAudioWPatch loopback streams
are opened dormant and explicitly started. Callback work is bounded copying and nonblocking
queue submission; a dedicated writer owns PCM output and metrics. Queue overflow or a stream
error terminates the `record` loop and saves accepted blocks with a warning. An empty capture
is an error, not a successful empty artifact. `record` enforces the time limit; direct service
callers own scheduling and must call `stop()`.

`record` emits `{type:'progress',stage,levels:{mic:float,system:float},duration_ms:int}`.
Levels and artifact peak/RMS are linear amplitudes, not percentages. Results are finalized WAVs,
not model output. Duration comes from actual PCM frames; record duration is the maximum track
duration, never their sum. WAV size constraints are checked before starting.

All files share a monotonic start epoch. Immediately before each sequential native `start()`,
the service records its offset from that epoch, rounded to that track's sample rate. The writer
prepends that many zero frames before the first accepted PCM block. Results expose
`start_offset_frames` and `start_offset_ms` (the silence is **already present** in the WAV;
consumers must not apply it again). This compensates sequential start-call delays, not unknown
latency inside a native start, device/driver buffering, or independent hardware clock drift.
Callback hardware timestamps are not used here. Long recordings are **not** guaranteed
sample-synchronous; no hour-long drift correction is implemented. A delayed-start regression
verifies a simultaneous tone on both files with the system source starting 200 ms later.

An exception reading `stream.active` is handled like a disconnected stream: stop, finalize
both writers, and return every confirmed nonempty track with a warning, not a lost result.

## Local transcription and mixing

`transcribe` accepts either `path` or `paths: [absolute_path, ...]` (one or two sources).
`paths` takes precedence. Optional `output_dir` is an absolute staging directory. A private
subdirectory is created and removed on completion/error/cancellation. Without `output_dir`,
Python's temporary-directory configuration is used. No source is modified.

Every source gets its own PyAV resampler. A bounded-block mix yields 16 kHz mono PCM16;
shorter tracks are padded, not concatenated. The mix averages both sources to avoid clipping.
The STT model receives this staging WAV, never just the microphone source.

`model_path` must name an existing local directory containing `tokenizer.json`. The tokenizer
check matters: faster-whisper can call `Tokenizer.from_pretrained()` when the directory lacks
that file **even if `local_files_only=True` was passed**. Offline environment flags are set
inside the worker. No model assets are downloaded. Only `int8` is accepted. CPU fallback happens
only for `device='auto'`, including errors encountered while consuming lazy model segments;
partial GPU segments are discarded. Cancellation never triggers fallback.

Local STT accepts at most **1800 seconds (30 minutes)** per aligned source / resulting mix.
Container duration is checked first, and decoded/resampled frame counts enforce the same bound
when metadata is missing or wrong. Both checks happen before importing/constructing WhisperModel
or inference. Excess duration raises an actionable `ValueError` asking to split the recording;
staging is removed and originals remain untouched. Capture and `audio_info` retain their
**14400-second (4-hour)** limit (subject to the existing WAV/file/decoded-sample budgets).

The compact UTF-8 JSON **segments array**, including brackets, commas, IDs, timings and escaped
text, is limited to **900 KiB (921600 bytes)** during lazy iteration. The complete terminal
`{type:'result',result:{segments,model,device,warnings}}` JSON body is separately checked to be
**strictly less than 1 MiB** (maximum 1048575 bytes) before returning to runtime framing.
Excess raises `TranscriptLimitError(ValueError)` with split-recording guidance; it never truncates
text, returns a partial success, or retries on CPU. Iterator and staging cleanup still run.

## Local path validation

`sozvon.audio.paths.local_path(value) -> Path` accepts strings/`PurePath` values and checks an
absolute local name **without requiring existence or calling stat/is_file/is_dir/open/resolve**.
Call it before model checks and any filesystem access. Audio source, capture output, model and
explicit STT staging paths all use it. UNC (both slash styles), extended/device namespaces,
relative paths and URLs fail with `ValueError` before target IO on every OS. Windows additionally
queries `GetDriveTypeW` on the drive root and rejects mapped network/unknown/unavailable drives;
only removable/fixed/CD-ROM/RAM drive types pass. PureWindowsPath tests and mocked drive probes
run without Windows or network access. This is a path-name policy, not a symlink/reparse-point
sandbox or a detector for network filesystems mounted under ordinary POSIX paths.

## Verification boundaries

Tests use dormant-until-start native-style fakes, actual PCM WAV writing, independent PyAV
readback, spectral checks on both mixed sources and a real worker process for `audio_info`.
No valid local speech model was supplied to this implementation task: real inference and real
Windows mic/WASAPI capture still require the parent's Windows verification. Hardware-free
model tests replace only `WhisperModel`; they are not evidence of live speech recognition.

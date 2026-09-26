# Runtime integration

```python
from sozvon.runtime.host import WorkerError, WorkerHost

result = WorkerHost().run(
    "ping",
    {},
    timeout=120,
    stop_event=None,
    on_event=None,
)
```

- `WorkerError.code`: `invalid_request`, `unknown_operation`, `worker_start`,
  `worker_exit`, `protocol_error`, `operation_error`, `timeout`, `cancelled`.
- Production argv: `[sys.executable, '-m', 'sozvon', '--worker']`, or
  `[sys.executable, '--worker']` when `sys.frozen` is true. Dispatch this flag
  **before** importing UI/native modules: `return sozvon.runtime.worker.main()`.
- `WorkerHost(command=[...], stop_grace=2.0)` permits a **trusted** test/embedding
  argv override. Never expose `command` or executable selection to HTTP payloads.
- Exactly one worker operation per process. Operations are allowlisted in
  `protocol.py`; audio/LLM implementations are imported only on dispatch.
- Frames: four-byte big-endian body length, UTF-8 JSON object, 1 MiB maximum,
  depth 64 maximum. Initial EOF is clean; truncated frames are errors. No pickle,
  shell, resynchronisation, execute/eval, or automatic retries.
- Request: `{operation, payload}`. Control: `{"operation":"stop"}`.
  Events: `ready` with `pid`; handler `progress`; `result` with `result:dict`;
  `error` with `code` and `message`. Callbacks see copied events and must return
  promptly. Callbacks run on the caller's thread; an arbitrary hung callback
  cannot be preempted by this synchronous API.
- Do not close stdin immediately after a request: EOF cancels nonrecording work
  and asks recordings to finalize. `ping` alone permits immediately closed input.
  Explicit stop keeps the control reader alive to detect later host disconnects.
- Stop is graceful for recording; nonrecording results after cancellation are
  rejected. Timeout always fails, even if recording produces a result during
  cleanup. Host waits `stop_grace` after stop, then terminates/kills and reaps.
  EOF in the worker arms a separate three-second orphan-exit watchdog.
- Reader/writer threads prevent blocked stdin or stderr from defeating deadlines.
  Event queue: eight frames. Stderr tail: 64 KiB, never included in public errors.
  Error messages redact the payload's API key; handlers must still avoid logging
  keys. Stdout fd 1 (including native C writes) is redirected to stderr; a separate
  noninheritable fd carries protocol frames.
- Success requires one valid terminal result, clean protocol EOF and exit code 0.
  A result followed by malformed output, another event, a crash or a hang fails.
- Cleanup handles callback exceptions and uses a private POSIX process group.
  Windows terminates/reaps the direct worker; no Windows Job Object process-tree
  containment is claimed. This is process isolation, not an OS security sandbox.

## Verification scope

`tests/test_runtime*.py` exercise real subprocesses and pipes, including malformed
and truncated frames, native chatter, blocked input, stderr/progress floods,
stop/EOF, kill escalation, callback failure/mutation, safe argv and lazy dispatch.
Tests inject handler stubs only inside test subprocesses. Audio hardware and live
LLM/model correctness belong to their own integration tests. Frozen argv selection
is unit-tested; an actual packaged Windows executable still needs platform QA.

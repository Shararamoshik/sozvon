import sys
import threading

from sozvon.runtime.host import WorkerHost


def test_record_stop_gives_writer_time_to_finalize():
    script = """
import os, sys, time
from sozvon.runtime.framing import read_frame, write_frame
source, out = sys.stdin.buffer, sys.stdout.buffer
read_frame(source)
write_frame(out, {"type":"ready", "pid":os.getpid()})
read_frame(source)
time.sleep(2.3)
write_frame(out, {"type":"result", "result":{"saved":True}})
"""
    stop = threading.Event()
    host = WorkerHost(command=[sys.executable, "-u", "-c", script])
    result = host.run("record", {}, timeout=15, stop_event=stop,
                      on_event=lambda event: stop.set() if event["type"] == "ready" else None)
    assert result == {"saved": True}

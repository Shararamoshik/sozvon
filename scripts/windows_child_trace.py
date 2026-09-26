import faulthandler
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if '--child' in sys.argv:
    f = (ROOT / 'artifacts/child-trace.txt').open('w',encoding='utf-8')
    faulthandler.enable(f)
    faulthandler.dump_traceback_later(5,repeat=True,file=f)
    from sozvon.runtime.worker import main
    raise SystemExit(main())
else:
    from sozvon.runtime.host import WorkerHost
    host = WorkerHost(command=[sys.executable,str(Path(__file__).resolve()),'--child'])
    try:
        result=host.run('audio_info',{'path':str(ROOT/'tests/fixtures/hello_ru.wav')},timeout=12)
        print(result)
    except Exception as e:
        print(type(e).__name__)

import subprocess
import sys

cases={
 'plain':'import numpy; print("OK",flush=True)',
 'redirect':'import os; os.dup2(2,1); import numpy; print("OK",flush=True)',
 'thread_first':'import threading,os; threading.Thread(target=lambda:os.read(0,4),daemon=True).start(); import numpy; print("OK",flush=True)',
 'one_thread':'import os; os.environ["OPENBLAS_NUM_THREADS"]="1"; import numpy; print("OK",flush=True)',
 'preimport':'import numpy; import os,threading; threading.Thread(target=lambda:os.read(0,4),daemon=True).start(); print("OK",flush=True)',
}
for mode,body in cases.items():
    process=subprocess.Popen([sys.executable,'-u','-c',body],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        process.wait(6)
        result='exit '+str(process.returncode)
    except subprocess.TimeoutExpired:
        result='TIMEOUT';process.kill();process.wait()
    print(mode,result,'OUT',process.stdout.read().decode(errors='replace'),'ERR',process.stderr.read().decode(errors='replace'))
    process.stdin.close();process.stdout.close();process.stderr.close()

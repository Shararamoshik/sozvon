"""A/B импорт NumPy с блокирующим чтением Windows CRT против raw ReadFile."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
prelude = '''
import os,sys,threading,time
'''
cases = {
 'crt': '''
threading.Thread(target=lambda:os.read(0,4),daemon=True).start()
time.sleep(.1)
import numpy
print('IMPORTED',flush=True)
''',
 'win32': '''
import ctypes,msvcrt
from ctypes import wintypes
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.ReadFile.argtypes=[wintypes.HANDLE,wintypes.LPVOID,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),wintypes.LPVOID]
k.ReadFile.restype=wintypes.BOOL
h=msvcrt.get_osfhandle(0)
def read():
 b=ctypes.create_string_buffer(4); n=wintypes.DWORD()
 k.ReadFile(h,b,4,ctypes.byref(n),None)
threading.Thread(target=read,daemon=True).start()
time.sleep(.1)
import numpy
print('IMPORTED',flush=True)
'''
}
for mode,body in cases.items():
    process=subprocess.Popen([sys.executable,'-c',prelude+body],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        process.wait(8)
        result='exit '+str(process.returncode)
    except subprocess.TimeoutExpired:
        result='TIMEOUT';process.kill();process.wait()
    print(mode,result,process.stdout.read().decode(errors='replace'))
    process.stdin.close();process.stdout.close();process.stderr.close()

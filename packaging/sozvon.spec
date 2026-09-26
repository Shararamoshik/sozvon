# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

root = Path(SPECPATH).parent
bundled = ["av", "ctranslate2", "faster_whisper", "tokenizers", "onnxruntime", "sounddevice", "pyaudiowpatch", "reportlab"]
datas = [(str(root / "sozvon" / "web" / "templates"), "sozvon/web/templates"),
         (str(root / "sozvon" / "web" / "static"), "sozvon/web/static")]
binaries = []
hiddenimports = collect_submodules("sozvon") + ["keyring.backends.Windows"]
for package in bundled:
    data, binary, hidden = collect_all(package)
    datas += data
    binaries += binary
    hiddenimports += hidden
analysis = Analysis([str(root / "packaging" / "entry.py")], pathex=[str(root)],
                    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
                    excludes=["pytest", "playwright", "ruff", "pypdf", "pypdfium2"], noarchive=False)
archive = PYZ(analysis.pure)
exe = EXE(archive, analysis.scripts, [], exclude_binaries=True, name="Sozvon",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
collection = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="Sozvon")

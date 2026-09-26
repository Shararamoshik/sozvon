"""Lexical local-path checks before filesystem access (including Windows network drives)."""

import sys
from pathlib import Path, PurePath, PureWindowsPath


def _windows_drive_type(root: str) -> int:
    """Query the drive category only; never stat/open a potentially remote target."""
    import ctypes
    from ctypes import wintypes

    function = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
    function.argtypes = [wintypes.LPCWSTR]
    function.restype = wintypes.UINT
    return int(function(root))


def local_path(value) -> Path:
    """Require an absolute local name, not existence; raise ValueError before target IO.

    UNC and device namespaces are rejected on every OS. On Windows only verified local
    drive roots are accepted (removable/fixed/CD-ROM/RAM); mapped and unknown drives fail
    closed. This validates path names, not symlink/reparse-point destinations or POSIX mounts.
    """
    error = "Нужен абсолютный локальный путь; сетевые пути и пространства устройств запрещены"
    if not isinstance(value, (str, PurePath)) or not str(value) or "\0" in str(value):
        raise ValueError(error)
    name = str(value)
    normalized = name.replace("\\", "/").lower()
    if normalized.startswith(("//", "/??/", "/device/")):
        raise ValueError(error)
    if sys.platform == "win32":
        windows = PureWindowsPath(name)
        if not windows.is_absolute() or len(windows.drive) != 2 or windows.drive[1] != ":":
            raise ValueError(error)
        try:
            drive_type = _windows_drive_type(windows.anchor)
        except (OSError, AttributeError) as exc:
            raise ValueError(error) from exc
        if drive_type not in {2, 3, 5, 6}:
            raise ValueError(error)
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(error)
    return path

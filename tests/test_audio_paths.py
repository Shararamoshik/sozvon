"""Reject non-local names before any filesystem or network-capable native IO."""

import threading
from pathlib import Path, PureWindowsPath

import pytest

NETWORK_NAMES = [
    "//server/share/audio.wav",
    r"\\server\share\audio.wav",
    r"\\?\UNC\server\share\audio.wav",
    r"\\?\C:\audio.wav",
    r"\\.\pipe\audio",
    r"\??\UNC\server\share\audio.wav",
    PureWindowsPath(r"\\server\share\audio.wav"),
    PureWindowsPath(r"\\?\UNC\server\share\audio.wav"),
    PureWindowsPath(r"\\?\C:\audio.wav"),
    PureWindowsPath(r"\\.\pipe\audio"),
    r"/\server\share\audio.wav",
    r"\Device\Mup\server\share\audio.wav",
]


@pytest.mark.parametrize("name", NETWORK_NAMES)
@pytest.mark.parametrize("entry", ["probe", "capture", "model", "staging"])
def test_audio_entry_points_reject_network_names_before_filesystem(
    name, entry, tmp_path, monkeypatch
):
    from test_audio_stt import stt_payload

    from sozvon.audio.capture import CaptureService
    from sozvon.audio.mix import prepared_audio
    from sozvon.audio.probe import local_audio_path
    from sozvon.audio.stt import transcribe

    payload = stt_payload(tmp_path)

    def guard(self, *args, **kwargs):
        if str(self) == str(name):
            pytest.fail("non-local path reached filesystem access")
        return originals[kwargs.pop("method")](self, *args, **kwargs)

    originals = {
        method: getattr(Path, method) for method in ("stat", "is_file", "is_dir", "open", "mkdir")
    }
    for method in originals:
        monkeypatch.setattr(
            Path, method, lambda self, *a, _m=method, **kw: guard(self, *a, method=_m, **kw)
        )
    with pytest.raises(ValueError, match="локальн"):
        if entry == "probe":
            local_audio_path(name)
        elif entry == "capture":
            CaptureService({"output_dir": name}, backends={})
        elif entry == "model":
            transcribe({**payload, "model_path": name}, threading.Event(), lambda _: None)
        else:
            with prepared_audio({**payload, "output_dir": name}, threading.Event()):
                pytest.fail("non-local staging accepted")


def test_local_path_validates_without_requiring_existence(tmp_path, monkeypatch):
    from sozvon.audio.paths import local_path

    target = tmp_path / "not-created" / "model"

    def forbidden(*args, **kwargs):
        pytest.fail("local_path must not access the filesystem")

    with monkeypatch.context() as patch:
        for method in ("stat", "is_file", "is_dir", "open", "exists", "resolve"):
            patch.setattr(Path, method, forbidden)
        assert local_path(str(target)) == target
        assert local_path(target) == target
        for value in ("relative", "", None, 42, "https://host/model", "C:relative"):
            with pytest.raises(ValueError, match="локальн"):
                local_path(value)


@pytest.mark.parametrize("drive_type", [0, 1, 4])
def test_windows_mapped_or_unknown_drives_rejected_before_filesystem(monkeypatch, drive_type):
    from sozvon.audio import paths

    probes = []
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setattr(
        paths, "_windows_drive_type", lambda root: probes.append(root) or drive_type
    )

    def forbidden(*args, **kwargs):
        pytest.fail("mapped drive reached filesystem")

    with monkeypatch.context() as patch:
        for method in ("stat", "is_file", "is_dir", "open"):
            patch.setattr(Path, method, forbidden)
        with pytest.raises(ValueError, match="локальн"):
            paths.local_path(PureWindowsPath(r"Z:\share\audio.wav"))
    assert probes == ["Z:\\"]


@pytest.mark.parametrize("drive_type", [2, 3, 5, 6])
def test_windows_local_drive_type_passes_without_target_io(monkeypatch, drive_type):
    from sozvon.audio import paths

    target = PureWindowsPath(r"C:\not-created\model")
    probes = []
    monkeypatch.setattr(paths.sys, "platform", "win32")
    # Exercise Windows parsing on POSIX without constructing an unsupported WindowsPath.
    monkeypatch.setattr(paths, "Path", PureWindowsPath)
    monkeypatch.setattr(
        paths, "_windows_drive_type", lambda root: probes.append(root) or drive_type
    )
    assert paths.local_path(target) == target
    assert probes == ["C:\\"]


def test_windows_drive_probe_uses_root_only_and_native_wide_signature(monkeypatch):
    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace

    from sozvon.audio.paths import _windows_drive_type

    calls = []

    def get_drive_type(root):
        calls.append(root)
        return 4  # DRIVE_REMOTE

    def library(name, **kwargs):
        assert name == "kernel32" and kwargs == {"use_last_error": True}
        return SimpleNamespace(GetDriveTypeW=get_drive_type)

    monkeypatch.setattr(ctypes, "WinDLL", library, raising=False)
    assert _windows_drive_type("Z:\\") == 4
    assert calls == ["Z:\\"]
    assert get_drive_type.argtypes == [wintypes.LPCWSTR]
    assert get_drive_type.restype == wintypes.UINT

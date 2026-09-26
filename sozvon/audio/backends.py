"""Native capture adapters. No sounddevice/PyAudioWPatch import at module scope."""

import sys


def _index(device, prefix):
    if not isinstance(device, str) or not device.startswith(prefix + ":"):
        raise ValueError(f"Неверный идентификатор устройства {prefix}")
    value = device[len(prefix) + 1 :]
    if not value.isascii() or not value.isdigit():
        raise ValueError("Неверный номер аудиоустройства")
    return int(value)


class SoundDeviceBackend:
    def __init__(self):
        import sounddevice

        self.module = sounddevice

    def devices(self):
        return [
            {"id": f"mic:{i}", "name": str(d["name"])}
            for i, d in enumerate(self.module.query_devices())
            if d["max_input_channels"] > 0
        ]

    def default_device(self):
        index = int(self.module.default.device[0])
        return f"mic:{index}" if index >= 0 else None

    def format(self, device):
        index = _index(device, "mic")
        info = self.module.query_devices(index, "input")
        rate, channels = round(info["default_samplerate"]), min(2, int(info["max_input_channels"]))
        self.module.check_input_settings(
            device=index, samplerate=rate, channels=channels, dtype="int16"
        )
        return rate, channels

    def open(self, device, rate, channels, callback):
        return self.module.RawInputStream(
            device=_index(device, "mic"),
            samplerate=rate,
            channels=channels,
            dtype="int16",
            blocksize=1024,
            callback=lambda data, frames, timing, status: callback(data, status),
        )

    def close(self):
        pass


class _WasapiStream:
    def __init__(self, stream):
        self.stream = stream

    @property
    def active(self):
        return self.stream.is_active()

    def start(self):
        self.stream.start_stream()

    def stop(self):
        self.stream.stop_stream()

    def close(self):
        self.stream.close()


class WasapiBackend:
    def __init__(self):
        import pyaudiowpatch

        self.module = pyaudiowpatch
        self.audio = pyaudiowpatch.PyAudio()

    def devices(self):
        return [
            {"id": f"loop:{int(d['index'])}", "name": str(d["name"])}
            for d in self.audio.get_loopback_device_info_generator()
        ]

    def default_device(self):
        try:
            return f"loop:{int(self.audio.get_default_wasapi_loopback()['index'])}"
        except (OSError, RuntimeError, ValueError, LookupError):
            return None

    def format(self, device):
        index = _index(device, "loop")
        info = self.audio.get_device_info_by_index(index)
        rate = round(info["defaultSampleRate"])
        channels = int(info["maxInputChannels"])
        if not self.audio.is_format_supported(
            rate, input_device=index, input_channels=channels, input_format=self.module.paInt16
        ):
            raise RuntimeError("Формат WASAPI loopback не поддерживается")
        return rate, channels

    def open(self, device, rate, channels, callback):
        def native_callback(data, frames, timing, status):
            callback(data, status)
            return None, self.module.paContinue

        stream = self.audio.open(
            format=self.module.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=_index(device, "loop"),
            frames_per_buffer=1024,
            stream_callback=native_callback,
            start=False,
        )
        return _WasapiStream(stream)

    def close(self):
        self.audio.terminate()


def create_backends():
    backends, warnings = {}, []
    for source, factory, label in (
        ("mic", SoundDeviceBackend, "Микрофон (sounddevice)"),
        ("system", WasapiBackend, "Системный звук (WASAPI loopback)"),
    ):
        if source == "system" and sys.platform != "win32":
            warnings.append("Системный звук WASAPI loopback доступен только на Windows")
            continue
        try:
            backends[source] = factory()
        except ImportError:
            warnings.append(f"{label}: модуль не установлен")
        except OSError:
            warnings.append(f"{label}: системная библиотека PortAudio не загрузилась")
        except Exception:  # noqa: BLE001 — native PortAudio errors inherit Exception directly
            warnings.append(f"{label}: не удалось инициализировать аудиобиблиотеку")
    return backends, warnings


def devices():
    backends, reasons = create_backends()
    result = {
        "available": False,
        "reason": "",
        "microphones": [],
        "outputs": [],
        "default_input": None,
        "default_output": None,
    }
    for source, backend in backends.items():
        field, default = (
            ("microphones", "default_input") if source == "mic" else ("outputs", "default_output")
        )
        try:
            result[field] = backend.devices()
            selected = backend.default_device()
            if selected in {d["id"] for d in result[field]}:
                result[default] = selected
            if not result[field]:
                reasons.append(
                    "Нет устройств микрофона"
                    if source == "mic"
                    else "Не найдено устройство WASAPI loopback"
                )
        except Exception:  # noqa: BLE001 — native PortAudio errors inherit Exception directly
            reasons.append(f"Не удалось получить список устройств: {source}")
        finally:
            try:
                backend.close()
            except Exception:  # noqa: BLE001 — native cleanup must not hide remaining devices
                reasons.append(f"Не удалось закрыть аудиобиблиотеку: {source}")
    result["available"] = bool(result["microphones"] and result["outputs"])
    result["reason"] = "; ".join(reasons)
    return result

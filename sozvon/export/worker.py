"""Fixed-file export operation; only result metadata crosses IPC.

The parent must create a private, single-use local job directory and delete it
when finished (also after a forced worker kill). Fixed names and link checks are
defence in depth, not a sandbox against an attacker owning the local filesystem.
Cancellation is checked around rendering; WorkerHost enforces the hard deadline.
"""

import json
import os
import stat

from sozvon.audio.paths import local_path
from sozvon.export.document import Block, ExportDocument


def render_export(payload, stop_event, emit):
    if not isinstance(payload, dict) or set(payload) != {"job_dir", "format"}:
        raise ValueError("Некорректные параметры экспорта")
    if not isinstance(payload["format"], str) or payload["format"] not in {"docx", "pdf"}:
        raise ValueError("Неизвестный формат экспорта")

    def check_cancelled():
        if stop_event.is_set():
            raise RuntimeError("Экспорт отменён")

    check_cancelled()
    folder = local_path(payload["job_dir"])
    if (not folder.is_dir() or ".." in folder.parts
            or any(path.is_symlink() or path.is_junction() for path in (folder, *folder.parents))):
        raise ValueError("Нужна локальная папка экспорта без ссылок")
    source = folder / "input.json"
    for name in ("input.json", "output.part", "output." + payload["format"]):
        path = folder / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.is_junction():
            raise ValueError("Нужен обычный файл без ссылок")
    expected = source.lstat()
    with source.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or not os.path.samestat(expected, opened)):
            raise ValueError("Входной файл экспорта изменился")
        if opened.st_size > 8 * 1024 * 1024:
            raise ValueError("Документ слишком большой для экспорта")
        data = stream.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("Документ слишком большой для экспорта")

    def unique_object(pairs):
        value = {}
        for key, entry in pairs:
            if key in value:
                raise ValueError("duplicate field")
            value[key] = entry
        return value

    def reject_constant(_):
        raise ValueError("nonfinite number")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object,
                           parse_constant=reject_constant)
    except (ValueError, RecursionError):
        raise ValueError("Некорректный экспортный снимок") from None
    if not isinstance(value, dict) or set(value) != {"title", "blocks"}:
        raise ValueError("Некорректный экспортный снимок")
    if not isinstance(value["blocks"], list) or not 1 <= len(value["blocks"]) <= 20_000:
        raise ValueError("Недопустимое количество блоков")
    for item in value["blocks"]:
        if not isinstance(item, dict) or set(item) != {"kind", "text"}:
            raise ValueError("Некорректный блок экспортного снимка")
    document = ExportDocument(value["title"], tuple(Block(**item) for item in value["blocks"]))
    check_cancelled()
    emit({"type": "progress", "stage": "Создание документа"})
    check_cancelled()
    temporary = folder / "output.part"
    fmt = payload["format"]
    target = folder / ("output." + fmt)
    try:
        output = temporary.open("x+b")
    except FileExistsError:
        raise ValueError("Временный файл экспорта уже занят") from None
    try:
        if fmt == "pdf":
            from pathlib import Path

            from sozvon.export.pdf import render_pdf

            fonts = Path(__file__).resolve().parents[1] / "web" / "static" / "fonts"
            render_pdf(document, output, fonts)
        else:
            from sozvon.export.docx import render_docx

            render_docx(document, output)
        output.flush()
        info = os.fstat(output.fileno())
        on_disk = temporary.lstat()
        if (not stat.S_ISREG(on_disk.st_mode) or on_disk.st_nlink != 1
                or not os.path.samestat(info, on_disk)):
            raise ValueError("Временный файл экспорта изменился")
        if info.st_size > 32 * 1024 * 1024:
            raise ValueError("Готовый документ слишком большой")
        output.close()
        check_cancelled()
        os.replace(temporary, target)
        return {"filename": target.name, "size": info.st_size}
    finally:
        output.close()
        temporary.unlink(missing_ok=True)

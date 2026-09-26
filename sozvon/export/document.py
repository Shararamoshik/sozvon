"""Immutable content shared by independent document renderers."""

from dataclasses import dataclass
from datetime import datetime

from sozvon.core.errors import ResourceConflict
from sozvon.templates.rendering import sections_for_report


def _text_bytes(value: str) -> int:
    if not isinstance(value, str):
        raise ValueError("В документе ожидался текст")  # noqa: TRY004 -- safe validation boundary
    if any((ord(char) < 32 and char not in "\n\r\t")
           or 0xD800 <= ord(char) <= 0xDFFF or ord(char) in {0xFFFE, 0xFFFF}
           for char in value):
        raise ValueError("Недопустимые символы документа")
    return len(value.encode("utf-8"))


@dataclass(frozen=True)
class Block:
    kind: str
    text: str

    def __post_init__(self):
        if not isinstance(self.kind, str) or self.kind not in {"h2", "p", "meta", "quote"}:
            raise ValueError("Неизвестный блок документа")
        _text_bytes(self.text)


@dataclass(frozen=True)
class ExportDocument:
    title: str
    blocks: tuple[Block, ...]

    def __post_init__(self):
        size = _text_bytes(self.title)
        if not self.title.strip():
            raise ValueError("Не задан заголовок документа")
        if (not isinstance(self.blocks, (list, tuple)) or not 1 <= len(self.blocks) <= 20_000
                or any(not isinstance(block, Block) for block in self.blocks)):
            raise ValueError("Некорректные блоки документа")
        for block in self.blocks:
            size += len(block.text.encode("utf-8"))
            if size > 8 * 1024 * 1024:
                raise ValueError("Документ слишком большой для экспорта")
        object.__setattr__(self, "blocks", tuple(self.blocks))


def anchor(segment: dict, ordinal: int) -> str:
    value = segment.get("start_ms")
    if value is None:
        return f"Абзац {ordinal + 1}"
    hours, rest = divmod(value // 1000, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def build_document(item, *, content="auto", include_transcript=False, include_quotes=True,
                   report_id=None) -> ExportDocument:
    """Build only from one coherent detail snapshot, never from live template state."""
    if content == "auto":
        content = "report" if item["reports"] or report_id is not None else "transcript"
    if content not in {"report", "transcript"}:
        raise ValueError("Неизвестный состав экспорта")
    blocks = []
    segments = item["segments"]
    if content == "report":
        reports = item["reports"]
        report = (next((r for r in reports if r["id"] == report_id), None)
                  if report_id is not None else (reports[0] if reports else None))
        if report is None:
            raise KeyError("report")
        if report["stale"] or report["transcript_revision"] != item["transcript_revision"]:
            raise ResourceConflict("Отчёт устарел: сначала создайте новую версию")
        created = datetime.fromisoformat(report["created_at"])
        when = created.strftime("%d.%m.%Y %H:%M")
        offset = created.utcoffset()
        if offset is not None:
            when += " " + ("UTC" if offset.total_seconds() == 0
                            else "UTC" + created.strftime("%z"))
        blocks.append(Block("meta", f"Отчёт создан: {when}"))
        index = {s["id"]: (s, i) for i, s in enumerate(segments)}
        for section in sections_for_report(report):
            blocks.append(Block("h2", section["title"]))
            if not section["items"]:
                blocks.append(Block("p", "В записи не найдено"))
            for entry in section["items"]:
                blocks.append(Block("p", entry["text"]))
                if section["kind"] == "tasks":
                    blocks.append(Block("meta", "Исполнитель: " + (entry.get("owner") or "Не назначен")
                                        + " · Срок: " + (entry.get("due") or "Не указан")))
                for evidence in entry["evidence"]:
                    if evidence["segment_id"] not in index:
                        raise ValueError("Источник отчёта не найден в его расшифровке")
                    segment, ordinal = index[evidence["segment_id"]]
                    blocks.append(Block("quote" if include_quotes else "meta",
                                        f"[{anchor(segment, ordinal)}] "
                                        + (evidence["quote"] if include_quotes else "Источник")))
    if content == "transcript" or include_transcript:
        if not segments:
            raise ValueError("Нет расшифровки для экспорта")
        blocks.append(Block("h2", "Расшифровка"))
        for i, segment in enumerate(segments):
            who = f" · {segment['speaker']}" if segment.get("speaker") else ""
            blocks.append(Block("meta", f"[{anchor(segment, i)}]{who}"))
            blocks.append(Block("p", segment["text"]))
    return ExportDocument(item["title"], tuple(blocks))

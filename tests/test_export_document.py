"""Synthetic document model checks; no meeting data or model calls."""

from dataclasses import FrozenInstanceError

import pytest


def test_document_and_blocks_are_immutable():
    from sozvon.export.document import Block, ExportDocument

    block = Block("p", "Синтетический текст")
    document = ExportDocument("Проверка", (block,))
    with pytest.raises(FrozenInstanceError):
        document.title = "Другое"
    with pytest.raises(FrozenInstanceError):
        block.text = "Другое"
    assert document.blocks == (block,)


def test_document_detaches_mutable_block_container():
    from sozvon.export.document import Block, ExportDocument

    blocks = [Block("p", "Исходный текст")]
    document = ExportDocument("Проверка", blocks)
    blocks.clear()
    assert document.blocks == (Block("p", "Исходный текст"),)


@pytest.mark.parametrize("kind,text", [
    ("html", "Обычный текст"), ("p", 7), ([], "Текст"),
    ("p", "Нуль\x00"), ("p", "\ud800"), ("p", "\uffff"), ("p", "\ufffe"),
])
def test_block_rejects_invalid_literal_text(kind, text):
    from sozvon.export.document import Block

    with pytest.raises(ValueError):
        Block(kind, text)


@pytest.mark.parametrize("title,blocks", [
    ("", ()), (" \n", ()), (7, ()), ("\uffff", ()),
    ("Текст", ()), ("Текст", ["не блок"]), ("Текст", None),
])
def test_document_rejects_invalid_shape(title, blocks):
    from sozvon.export.document import ExportDocument

    with pytest.raises(ValueError):
        ExportDocument(title, blocks)


def test_document_is_bounded_in_utf8_bytes_and_block_count():
    from sozvon.export.document import Block, ExportDocument

    block = Block("p", "Я")
    with pytest.raises(ValueError, match="блок"):
        ExportDocument("Проверка", (block,) * 20_001)
    with pytest.raises(ValueError, match="больш"):
        ExportDocument("Проверка", (Block("p", "Я" * (4 * 1024 * 1024)),))

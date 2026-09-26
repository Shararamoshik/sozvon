"""Markdown presentation of the same document used by DOCX and PDF."""

from sozvon.export.document import ExportDocument


def render_markdown(document: ExportDocument) -> bytes:
    lines = ["# " + document.title, ""]
    for block in document.blocks:
        if block.kind == "h2":
            lines.append("## " + block.text)
        elif block.kind == "quote":
            lines.extend("> " + line for line in block.text.splitlines())
        else:
            lines.append(block.text)
        lines.append("")
    return "\n".join(lines).encode("utf-8")

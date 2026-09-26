"""Lossless archive of one repository snapshot; presentation options do not filter it."""

import json


def render_json(item: dict) -> bytes:
    return json.dumps(item, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")

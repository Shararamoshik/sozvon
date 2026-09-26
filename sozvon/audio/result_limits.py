"""Shared compact UTF-8 result budget for local and cloud recognition."""

import json

MAX_SEGMENTS_BYTES = 900 * 1024
MAX_RESULT_BYTES = 1024 * 1024 - 1


def json_size(value):
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    )

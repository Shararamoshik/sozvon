import pytest


def test_segment_time_is_converted_from_real_response():
    from sozvon.audio.cloud_response import normalize_response

    rows, warnings = normalize_response(
        {
            "segments": [
                {"text": " Привет ", "start": 1.25, "end": 2.5},
                {"text": "Снова", "start": 3, "end": 4},
            ]
        },
        5000,
    )
    assert (rows[0]["start_ms"], rows[0]["end_ms"]) == (1250, 2500)
    assert rows[0]["text"] == "Привет"
    assert rows[1]["ordinal"] == 1
    assert rows[0]["id"] != rows[1]["id"]
    assert rows[0]["speaker"] is None
    assert warnings == []


@pytest.mark.parametrize(
    "start,end",
    [
        (2, 1),
        (-1, 1),
        (float("nan"), 1),
        (0, 9),
        (True, 1),
        (0, "1"),
        (0, float("inf")),
        (None, None),
        (0, 10**1000),
    ],
)
def test_invalid_times_are_rejected(start, end):
    from sozvon.audio.cloud_response import normalize_response

    with pytest.raises(ValueError, match="временные"):
        normalize_response({"segments": [{"text": "Речь", "start": start, "end": end}]}, 5000)


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {"text": 1},
        {"text": "  "},
        {},
        {"segments": "bad", "text": "fallback"},
        {"segments": [None], "text": "fallback"},
        {"segments": [{"text": 1, "start": 0, "end": 1}]},
        {"text": "hello\x00"},
        {"text": "hello\x7f"},
        {"text": "hello\ud800"},
        {"segments": [{"text": "bad\x01", "start": 0, "end": 1}]},
        {"segments": [{"text": " ", "start": -1, "end": 1}], "text": "fallback"},
    ],
)
def test_invalid_response_types_and_control_characters_are_rejected(data):
    from sozvon.audio.cloud_response import normalize_response

    with pytest.raises(ValueError):
        normalize_response(data, 5000)


def test_serialized_result_limits_include_utf8_escaping_and_envelope():
    from sozvon.audio.cloud_response import normalize_response, validate_result_size
    from sozvon.audio.result_limits import MAX_RESULT_BYTES, MAX_SEGMENTS_BYTES, json_size

    for text in ["я" * (MAX_SEGMENTS_BYTES // 2), "x\t" * (MAX_SEGMENTS_BYTES // 3)]:
        with pytest.raises(ValueError, match="не обрезан"):
            normalize_response({"text": text}, 5000)
    with pytest.raises(ValueError, match="канал"):
        validate_result_size({"metadata": "x" * (MAX_RESULT_BYTES - 15)})
    assert json_size({"x": "я"}) == len('{"x":"я"}'.encode())


def test_segment_budget_stops_before_processing_remaining_rows(monkeypatch):
    from sozvon.audio import cloud_response

    monkeypatch.setattr(cloud_response, "MAX_SEGMENTS_BYTES", 200)
    with pytest.raises(ValueError, match="не обрезан"):
        cloud_response.normalize_response(
            {"segments": [{"text": "я" * 100, "start": 0, "end": 1}, None]}, 5000
        )


@pytest.mark.parametrize(
    "data",
    [
        {"segments": [], "text": "Fallback"},
        {"segments": [{"text": " ", "start": 0, "end": 1}], "text": "Fallback"},
    ],
)
def test_empty_segments_fall_back_to_untimed_text(data):
    from sozvon.audio.cloud_response import normalize_response

    rows, warnings = normalize_response(data, 5000)
    assert rows[0]["text"] == "Fallback" and rows[0]["start_ms"] is None
    assert warnings


def test_out_of_order_segments_do_not_silently_fall_back():
    from sozvon.audio.cloud_response import normalize_response

    with pytest.raises(ValueError, match="временные"):
        normalize_response(
            {
                "text": "Fallback",
                "segments": [
                    {"text": "a", "start": 2, "end": 3},
                    {"text": "b", "start": 1, "end": 2},
                ],
            },
            5000,
        )


def test_text_only_response_has_no_fabricated_time():
    from sozvon.audio.cloud_response import normalize_response

    rows, warnings = normalize_response({"text": "Срок пока не указан."}, 5000)
    assert rows[0]["start_ms"] is None
    assert rows[0]["end_ms"] is None
    assert rows[0]["speaker"] is None
    assert rows[0]["text"] == "Срок пока не указан."
    assert warnings

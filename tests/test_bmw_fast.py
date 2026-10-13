"""BMW-FAST telegram framing."""

import pytest

from e92_redline.transport.bmw_fast import (
    Frame,
    FrameError,
    FrameParser,
    build_frame,
    checksum,
    parse_frame,
)


def test_checksum_is_additive_not_xor():
    # 0x82+0x12+0xF1+0x01+0x0C = 0x192 -> 0x92 (XOR would give 0x6C)
    assert checksum(bytes([0x82, 0x12, 0xF1, 0x01, 0x0C])) == 0x92


def test_short_physical_frame():
    assert build_frame(0x12, bytes([0x01, 0x0C])) == bytes([0x82, 0x12, 0xF1, 0x01, 0x0C, 0x92])


def test_functional_frame_sets_mode_bits():
    frame = build_frame(0x33, bytes([0x01, 0x00]), functional=True)
    assert frame[0] == 0xC2
    assert parse_frame(frame).functional


def test_length_byte_for_64_to_255():
    payload = bytes(range(100))
    frame = build_frame(0x12, payload)
    assert frame[:4] == bytes([0x80, 0x12, 0xF1, 100])
    assert parse_frame(frame).payload == payload


def test_two_byte_length_above_255():
    payload = bytes(300)
    frame = build_frame(0x12, payload)
    assert frame[:6] == bytes([0x80, 0x12, 0xF1, 0x00, 0x01, 0x2C])
    assert parse_frame(frame).payload == payload


def test_empty_payload_rejected():
    with pytest.raises(FrameError):
        build_frame(0x12, b"")


def test_parser_handles_byte_by_byte_and_back_to_back():
    a = build_frame(0xF1, bytes([0x41, 0x0C, 0x1A, 0xF8]), source=0x12)
    b = build_frame(0xF1, bytes([0x41, 0x05, 0x82]), source=0x12)
    parser = FrameParser()
    frames: list[Frame] = []
    for byte in a + b:
        frames += parser.feed(bytes([byte]))
    assert [f.payload for f in frames] == [bytes([0x41, 0x0C, 0x1A, 0xF8]), bytes([0x41, 0x05, 0x82])]
    assert frames[0].source == 0x12 and frames[0].target == 0xF1
    assert parser.pending == 0


def test_parser_resyncs_after_garbage_and_bad_checksum():
    good = build_frame(0xF1, bytes([0x7E, 0x01]), source=0x12)
    bad = bytearray(good)
    bad[-1] ^= 0xFF
    parser = FrameParser()
    frames = parser.feed(b"\x00\x13" + bytes(bad) + good)
    assert [f.payload for f in frames] == [bytes([0x7E, 0x01])]
    assert parser.bad_frames >= 1


def test_response_pending_detection():
    assert Frame(0xF1, 0x12, bytes([0x7F, 0x22, 0x78])).is_response_pending()
    assert not Frame(0xF1, 0x12, bytes([0x7F, 0x22, 0x31])).is_response_pending()

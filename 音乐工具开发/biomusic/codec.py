from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass
from typing import Any, Iterable
from xml.etree import ElementTree


ROW_SIZE = 12
ROW_CAPACITY = math.factorial(ROW_SIZE)
CODEC_VERSION = "biosound-cantor-v1"


@dataclass(frozen=True)
class CodecSpec:
    data_type: str
    alphabet: str
    block_size: int
    pad_symbol: str

    @property
    def radix(self) -> int:
        return len(self.alphabet)

    @property
    def domain_size(self) -> int:
        return self.radix ** self.block_size


CODEC_SPECS = {
    "dna": CodecSpec("dna", "ACGT", 12, "A"),
    "rna": CodecSpec("rna", "ACGU", 12, "A"),
    # Canonical order: 20 standard amino acids followed by FASTA stop marker.
    "protein": CodecSpec("protein", "ACDEFGHIKLMNPQRSTVWY*", 6, "A"),
}


def rank_permutation(row: Iterable[int]) -> int:
    """Return the zero-based lexicographic rank of a 0..11 permutation."""
    values = [int(value) for value in row]
    if sorted(values) != list(range(ROW_SIZE)):
        raise ValueError("音列必须恰好包含 0–11，且每个音级只出现一次。")
    available = list(range(ROW_SIZE))
    rank = 0
    for index, value in enumerate(values):
        digit = available.index(value)
        rank += digit * math.factorial(ROW_SIZE - 1 - index)
        available.pop(digit)
    return rank


def unrank_permutation(rank: int) -> list[int]:
    """Return the 0..11 permutation at a zero-based lexicographic rank."""
    rank = int(rank)
    if not 0 <= rank < ROW_CAPACITY:
        raise ValueError(f"康托排名必须在 0–{ROW_CAPACITY - 1} 之间。")
    available = list(range(ROW_SIZE))
    row: list[int] = []
    remainder = rank
    for width in range(ROW_SIZE, 0, -1):
        factor = math.factorial(width - 1)
        digit, remainder = divmod(remainder, factor)
        row.append(available.pop(digit))
    return row


def transform_row(row: Iterable[int], form: str) -> list[int]:
    """Apply P/I/R/RI without discarding the carrier's absolute pitch classes."""
    values = [int(value) for value in row]
    if sorted(values) != list(range(ROW_SIZE)):
        raise ValueError("变形前的载体音列不是完整十二音列。")
    form = form.upper()
    if form not in {"P", "I", "R", "RI"}:
        raise ValueError("音列形式必须是 P、I、R 或 RI。")
    if "I" in form:
        values = [(-value) % ROW_SIZE for value in values]
    if "R" in form:
        values.reverse()
    return values


def inverse_transform_row(row: Iterable[int], form: str) -> list[int]:
    # Inversion and reversal commute and are both involutions.
    return transform_row(row, form)


def _digits_to_integer(symbols: str, alphabet: str) -> int:
    lookup = {symbol: value for value, symbol in enumerate(alphabet)}
    value = 0
    for symbol in symbols:
        value = value * len(alphabet) + lookup[symbol]
    return value


def _integer_to_symbols(value: int, spec: CodecSpec) -> str:
    digits = [0] * spec.block_size
    for index in range(spec.block_size - 1, -1, -1):
        value, digit = divmod(value, spec.radix)
        digits[index] = digit
    if value:
        raise ValueError("康托排名超出当前编解码器的合法域。")
    return "".join(spec.alphabet[digit] for digit in digits)


def encode_sequence(sequence: str, data_type: str, row_form: str = "P") -> tuple[list[list[int]], dict[str, Any]]:
    data_type = data_type.lower()
    if data_type not in CODEC_SPECS:
        raise ValueError("可逆十二音列仅支持 DNA、RNA 或标准蛋白质序列。")
    spec = CODEC_SPECS[data_type]
    sequence = "".join(sequence.split()).upper()
    illegal = sorted(set(sequence) - set(spec.alphabet))
    if illegal:
        allowed = "、".join(spec.alphabet)
        raise ValueError(
            f"{data_type.upper()} 可逆模式遇到未定义符号：{'、'.join(illegal)}。"
            f"当前 v1 字母表为 {allowed}；为保证无损，平台不会猜测或删除字符。"
        )
    if not sequence:
        raise ValueError("不能对空序列进行可逆编码。")
    if spec.domain_size > ROW_CAPACITY:
        raise AssertionError("编解码规格超过十二音列容量。")

    pad_length = (-len(sequence)) % spec.block_size
    padded = sequence + spec.pad_symbol * pad_length
    ranks: list[int] = []
    prime_rows: list[list[int]] = []
    presented_rows: list[list[int]] = []
    for start in range(0, len(padded), spec.block_size):
        block = padded[start:start + spec.block_size]
        rank = _digits_to_integer(block, spec.alphabet)
        prime = unrank_permutation(rank)
        ranks.append(rank)
        prime_rows.append(prime)
        presented_rows.append(transform_row(prime, row_form))

    metadata: dict[str, Any] = {
        "codec_version": CODEC_VERSION,
        "data_type": data_type,
        "alphabet": spec.alphabet,
        "radix": spec.radix,
        "block_size": spec.block_size,
        "row_size": ROW_SIZE,
        "row_form": row_form.upper(),
        "carrier_voice": "V1_melody",
        "block_count": len(presented_rows),
        "original_length": len(sequence),
        "pad_symbol": spec.pad_symbol,
        "pad_length": pad_length,
        "domain_size": spec.domain_size,
        "sequence_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest(),
        "ranking_order": "zero-based lexicographic Lehmer/Cantor rank",
        "pitch_class_order": "0=C, 1=C#, ..., 11=B",
        # Ranks are an audit certificate, not the source of truth during decoding.
        "source_ranks": ranks,
    }
    return presented_rows, metadata


def decode_rows(rows: Iterable[Iterable[int]], metadata: dict[str, Any], verify_checksum: bool = True) -> str:
    if metadata.get("codec_version") != CODEC_VERSION:
        raise ValueError(f"不支持的编解码版本：{metadata.get('codec_version')!r}。")
    data_type = str(metadata.get("data_type", "")).lower()
    if data_type not in CODEC_SPECS:
        raise ValueError("元数据缺少受支持的 DNA/RNA/蛋白质类型。")
    spec = CODEC_SPECS[data_type]
    if metadata.get("alphabet") != spec.alphabet or int(metadata.get("block_size", -1)) != spec.block_size:
        raise ValueError("元数据字母表或分块长度与当前编解码器版本不一致。")
    row_form = str(metadata.get("row_form", "P")).upper()
    row_list = [[int(value) for value in row] for row in rows]
    expected_blocks = int(metadata.get("block_count", len(row_list)))
    if len(row_list) != expected_blocks:
        raise ValueError(f"载体应有 {expected_blocks} 个音列块，实际读取到 {len(row_list)} 个。")

    decoded_blocks: list[str] = []
    for block_index, presented in enumerate(row_list):
        if sorted(presented) != list(range(ROW_SIZE)):
            raise ValueError(f"第 {block_index + 1} 块违反 H_permutation：不是 0–11 的完整排列。")
        prime = inverse_transform_row(presented, row_form)
        rank = rank_permutation(prime)
        if rank >= spec.domain_size:
            raise ValueError(
                f"第 {block_index + 1} 块违反 H_codec_domain：排名 {rank:,} 不在 0–{spec.domain_size - 1:,}。"
            )
        decoded_blocks.append(_integer_to_symbols(rank, spec))

    original_length = int(metadata.get("original_length", -1))
    padded = "".join(decoded_blocks)
    if not 0 <= original_length <= len(padded):
        raise ValueError("original_length 元数据无效，无法安全去除尾部填充。")
    sequence = padded[:original_length]
    expected_hash = metadata.get("sequence_sha256")
    actual_hash = hashlib.sha256(sequence.encode("ascii")).hexdigest()
    if verify_checksum and expected_hash and actual_hash != expected_hash:
        raise ValueError("序列校验和不一致：载体音列、顺序或元数据已被修改。")
    return sequence


def compact_codec_json(metadata: dict[str, Any]) -> str:
    """Compact metadata for embedding in MIDI and MusicXML."""
    return json.dumps(metadata, ensure_ascii=True, separators=(",", ":"))


def rows_from_events(events: Iterable[Any]) -> list[list[int]]:
    carrier = sorted(
        (event for event in events if event.voice_id == "V1_melody" and event.codec_block is not None),
        key=lambda event: (event.codec_block, event.row_position, event.onset, event.event_id),
    )
    rows: dict[int, list[tuple[int, int]]] = {}
    for event in carrier:
        rows.setdefault(int(event.codec_block), []).append((int(event.row_position), int(event.midi) % 12))
    output: list[list[int]] = []
    for block_index in sorted(rows):
        ordered = sorted(rows[block_index])
        if [position for position, _ in ordered] != list(range(ROW_SIZE)):
            raise ValueError(f"第 {block_index + 1} 块缺少载体音符或位置重复。")
        output.append([pitch_class for _, pitch_class in ordered])
    return output


def _rows_from_pitch_classes(pitch_classes: list[int], block_count: int) -> list[list[int]]:
    expected = block_count * ROW_SIZE
    if len(pitch_classes) != expected:
        raise ValueError(f"载体应包含 {expected} 个主声部音符，实际读取到 {len(pitch_classes)} 个。")
    return [pitch_classes[start:start + ROW_SIZE] for start in range(0, expected, ROW_SIZE)]


def _decode_json_artifact(data: bytes) -> tuple[str, dict[str, Any], list[list[int]]]:
    payload = json.loads(data.decode("utf-8-sig"))
    gvr = payload.get("gvr", payload)
    metadata = gvr.get("codec") or payload.get("codec")
    rows = gvr.get("tone_rows") or payload.get("tone_rows")
    if not metadata or rows is None:
        raise ValueError("JSON 中没有找到 codec 与 tone_rows。")
    sequence = decode_rows(rows, metadata, verify_checksum=True)
    return sequence, metadata, rows


def _decode_musicxml_artifact(data: bytes) -> tuple[str, dict[str, Any], list[list[int]]]:
    root = ElementTree.fromstring(data)
    field = root.find("./identification/miscellaneous/miscellaneous-field[@name='biosound-codec']")
    if field is None or not field.text:
        raise ValueError("MusicXML 中没有 BioSound 可逆编解码元数据。")
    metadata = json.loads(field.text)
    part = root.find("./part")
    if part is None:
        raise ValueError("MusicXML 中没有主旋律 Part。")
    step_pc = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    pitch_classes: list[int] = []
    for note in part.findall("./measure/note"):
        pitch = note.find("pitch")
        if pitch is None:
            continue
        # A note split across a barline is represented more than once; only its first piece carries data.
        if any(tie.get("type") == "stop" for tie in note.findall("tie")):
            continue
        step = pitch.findtext("step")
        alter = int(pitch.findtext("alter", "0"))
        if step not in step_pc:
            raise ValueError("MusicXML 主声部包含无法识别的音名。")
        pitch_classes.append((step_pc[step] + alter) % ROW_SIZE)
    rows = _rows_from_pitch_classes(pitch_classes, int(metadata["block_count"]))
    sequence = decode_rows(rows, metadata, verify_checksum=True)
    return sequence, metadata, rows


def _read_vlq(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    while True:
        if offset >= len(data):
            raise ValueError("MIDI 可变长度字段被截断。")
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, offset


def _parse_midi_track(track: bytes) -> tuple[list[int], dict[str, Any] | None]:
    offset = 0
    running_status: int | None = None
    note_ons: list[int] = []
    metadata: dict[str, Any] | None = None
    while offset < len(track):
        _, offset = _read_vlq(track, offset)
        if offset >= len(track):
            break
        status = track[offset]
        if status & 0x80:
            offset += 1
            running_status = status if status < 0xF0 else None
        elif running_status is not None:
            status = running_status
        else:
            raise ValueError("MIDI 运行状态无效。")

        if status == 0xFF:
            if offset >= len(track):
                raise ValueError("MIDI meta event 被截断。")
            meta_type = track[offset]
            offset += 1
            length, offset = _read_vlq(track, offset)
            payload = track[offset:offset + length]
            offset += length
            if meta_type == 0x01 and payload.startswith(b"BIOSOUND_CODEC:"):
                metadata = json.loads(payload[len(b"BIOSOUND_CODEC:"):].decode("ascii"))
            if meta_type == 0x2F:
                break
            continue
        if status in {0xF0, 0xF7}:
            length, offset = _read_vlq(track, offset)
            offset += length
            continue

        command = status & 0xF0
        data_length = 1 if command in {0xC0, 0xD0} else 2
        if offset + data_length > len(track):
            raise ValueError("MIDI channel event 被截断。")
        first = track[offset]
        second = track[offset + 1] if data_length == 2 else 0
        offset += data_length
        if command == 0x90 and second > 0:
            note_ons.append(first % ROW_SIZE)
    return note_ons, metadata


def _decode_midi_artifact(data: bytes) -> tuple[str, dict[str, Any], list[list[int]]]:
    if len(data) < 14 or data[:4] != b"MThd":
        raise ValueError("不是有效的标准 MIDI 文件。")
    header_length = struct.unpack(">I", data[4:8])[0]
    _, track_count, _ = struct.unpack(">HHH", data[8:14])
    offset = 8 + header_length
    tracks: list[bytes] = []
    for _ in range(track_count):
        if data[offset:offset + 4] != b"MTrk":
            raise ValueError("MIDI 轨道块缺失。")
        length = struct.unpack(">I", data[offset + 4:offset + 8])[0]
        start = offset + 8
        tracks.append(data[start:start + length])
        offset = start + length
    parsed = [_parse_midi_track(track) for track in tracks]
    metadata = next((meta for _, meta in parsed if meta), None)
    if metadata is None:
        raise ValueError("MIDI 中没有 BioSound 可逆编解码元数据。")
    # The exporter writes V1 immediately after the tempo/metadata track.
    carrier_notes = next((notes for notes, _ in parsed[1:] if notes), [])
    rows = _rows_from_pitch_classes(carrier_notes, int(metadata["block_count"]))
    sequence = decode_rows(rows, metadata, verify_checksum=True)
    return sequence, metadata, rows


def decode_artifact(filename: str, data: bytes) -> tuple[str, dict[str, Any], list[list[int]]]:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "json":
        return _decode_json_artifact(data)
    if suffix in {"musicxml", "xml"}:
        return _decode_musicxml_artifact(data)
    if suffix in {"mid", "midi"}:
        return _decode_midi_artifact(data)
    raise ValueError("解码器仅接受平台导出的 JSON、MusicXML 或 MIDI。")

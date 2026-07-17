from __future__ import annotations

import io
import json
import shutil
import struct
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from xml.etree.ElementTree import Element, ElementTree, SubElement, indent

from .codec import compact_codec_json
from .models import GVRReport, MusicEvent


PITCH_NAMES = [
    ("C", 0), ("C", 1), ("D", 0), ("D", 1), ("E", 0), ("F", 0),
    ("F", 1), ("G", 0), ("G", 1), ("A", 0), ("A", 1), ("B", 0),
]

ORCHESTRAL_PROGRAMS = {
    "violin": 40,
    "viola": 41,
    "cello": 42,
    "orchestral_harp": 46,
    "french_horn": 60,
    "oboe": 68,
    "bassoon": 70,
    "clarinet": 71,
    "flute": 73,
}

ORCHESTRAL_LABELS = {
    "violin": "Violin / 小提琴",
    "viola": "Viola / 中提琴",
    "cello": "Cello / 大提琴",
    "orchestral_harp": "Orchestral Harp / 竖琴",
    "french_horn": "French Horn / 圆号",
    "oboe": "Oboe / 双簧管",
    "bassoon": "Bassoon / 大管",
    "clarinet": "Clarinet / 单簧管",
    "flute": "Flute / 长笛",
}

VOICE_LABELS = {
    "V1_melody": "I. Biological melody / 生物主旋律",
    "V2_counterpoint": "II. String counterpoint / 弦乐对位",
    "V3_bass": "III. Structural bass / 结构低音",
    "V4_horn_harmony": "IV. Horn harmonic field / 圆号和声场",
    "V5_viola_harmony": "V. Viola inner harmony / 中提琴内声部",
    "V6_harp_accents": "VI. Harp structural accents / 竖琴结构重音",
}


def _voice_groups(events: list[MusicEvent]) -> list[tuple[str, list[MusicEvent]]]:
    groups: dict[str, list[MusicEvent]] = defaultdict(list)
    for event in events:
        groups[event.voice_id].append(event)
    return [
        (voice_id, sorted(group, key=lambda e: (e.onset, e.event_id)))
        for voice_id, group in sorted(groups.items(), key=lambda item: item[0])
    ]


def _note_type(quarter_beats: float) -> tuple[str, bool]:
    table = {
        0.25: ("16th", False), 0.5: ("eighth", False), 0.75: ("eighth", True),
        1.0: ("quarter", False), 1.5: ("quarter", True), 2.0: ("half", False),
        3.0: ("half", True), 4.0: ("whole", False),
    }
    return table.get(round(quarter_beats, 2), ("quarter", False))


def _timeline(events: list[MusicEvent]) -> list[dict]:
    tokens: list[dict] = []
    cursor = 0.0
    for event in sorted(events, key=lambda e: (e.onset, e.event_id)):
        onset = round(event.onset * 4) / 4
        duration = max(0.25, round(event.duration * 4) / 4)
        if onset > cursor + 1e-8:
            tokens.append({"rest": True, "duration": onset - cursor})
        tokens.append({"rest": False, "duration": duration, "event": event})
        cursor = max(cursor, onset) + duration
    return tokens


def _measures(events: list[MusicEvent], capacity: float) -> list[list[dict]]:
    measures: list[list[dict]] = []
    bar: list[dict] = []
    used = 0.0
    for token in _timeline(events):
        remaining = float(token["duration"])
        first_piece = True
        while remaining > 1e-8:
            room = capacity - used
            take = min(room, remaining)
            piece = dict(token)
            piece["duration"] = take
            piece["tie_stop"] = not first_piece and not token["rest"]
            remaining -= take
            piece["tie_start"] = remaining > 1e-8 and not token["rest"]
            piece["show_lyric"] = first_piece
            bar.append(piece)
            used += take
            first_piece = False
            if used >= capacity - 1e-8:
                measures.append(bar)
                bar = []
                used = 0.0
    if bar:
        if used < capacity:
            bar.append({"rest": True, "duration": capacity - used, "tie_start": False, "tie_stop": False, "show_lyric": False})
        measures.append(bar)
    return measures or [[{"rest": True, "duration": capacity, "tie_start": False, "tie_stop": False, "show_lyric": False}]]


def events_to_musicxml(
    events: list[MusicEvent],
    title: str,
    tempo: int,
    meter: tuple[int, int],
    codec_metadata: dict | None = None,
    sequence_metadata: dict | None = None,
) -> bytes:
    beats, beat_type = meter
    capacity = beats * 4.0 / beat_type
    voices = _voice_groups(events)
    score = Element("score-partwise", version="4.0")
    work = SubElement(score, "work")
    SubElement(work, "work-title").text = title
    identification = SubElement(score, "identification")
    SubElement(identification, "creator", type="software").text = "BioSound GVR Orchestral Edition"
    encoding = SubElement(identification, "encoding")
    SubElement(encoding, "software").text = "Traceable multimodal biological musification"
    if codec_metadata or sequence_metadata:
        miscellaneous = SubElement(identification, "miscellaneous")
        if codec_metadata:
            SubElement(miscellaneous, "miscellaneous-field", name="biosound-codec").text = compact_codec_json(codec_metadata)
        if sequence_metadata:
            SubElement(miscellaneous, "miscellaneous-field", name="biosound-sequence").text = compact_codec_json(sequence_metadata)
    part_list = SubElement(score, "part-list")

    for part_number, (voice_id, voice_events) in enumerate(voices, 1):
        part_id = f"P{part_number}"
        timbre = voice_events[0].timbre
        score_part = SubElement(part_list, "score-part", id=part_id)
        SubElement(score_part, "part-name").text = VOICE_LABELS.get(voice_id, voice_id)
        SubElement(score_part, "part-abbreviation").text = voice_id.replace("V", "V.")
        instrument = SubElement(score_part, "score-instrument", id=f"{part_id}-I1")
        SubElement(instrument, "instrument-name").text = ORCHESTRAL_LABELS.get(timbre, timbre)
        midi_instrument = SubElement(score_part, "midi-instrument", id=f"{part_id}-I1")
        midi_channel = part_number if part_number < 10 else part_number + 1
        SubElement(midi_instrument, "midi-channel").text = str(midi_channel)
        SubElement(midi_instrument, "midi-program").text = str(ORCHESTRAL_PROGRAMS.get(timbre, 73) + 1)

    for part_number, (voice_id, voice_events) in enumerate(voices, 1):
        part_id = f"P{part_number}"
        part = SubElement(score, "part", id=part_id)
        contents_by_measure = _measures(voice_events, capacity)
        is_bass = voice_id == "V3_bass"
        for number, contents in enumerate(contents_by_measure, 1):
            measure = SubElement(part, "measure", number=str(number))
            if number == 1:
                attrs = SubElement(measure, "attributes")
                SubElement(attrs, "divisions").text = "4"
                key = SubElement(attrs, "key")
                SubElement(key, "fifths").text = "0"
                time = SubElement(attrs, "time")
                SubElement(time, "beats").text = str(beats)
                SubElement(time, "beat-type").text = str(beat_type)
                clef = SubElement(attrs, "clef")
                SubElement(clef, "sign").text = "F" if is_bass else "G"
                SubElement(clef, "line").text = "4" if is_bass else "2"
                if part_number == 1:
                    direction = SubElement(measure, "direction", placement="above")
                    dtype = SubElement(direction, "direction-type")
                    metronome = SubElement(dtype, "metronome")
                    SubElement(metronome, "beat-unit").text = "quarter"
                    SubElement(metronome, "per-minute").text = str(tempo)
                    SubElement(direction, "sound", tempo=str(tempo))
            for piece in contents:
                note = SubElement(measure, "note")
                if piece["rest"]:
                    SubElement(note, "rest")
                else:
                    event = piece["event"]
                    step, alter = PITCH_NAMES[event.midi % 12]
                    pitch = SubElement(note, "pitch")
                    SubElement(pitch, "step").text = step
                    if alter:
                        SubElement(pitch, "alter").text = str(alter)
                    SubElement(pitch, "octave").text = str(event.midi // 12 - 1)
                SubElement(note, "duration").text = str(max(1, int(round(piece["duration"] * 4))))
                SubElement(note, "voice").text = "1"
                type_name, dotted = _note_type(piece["duration"])
                SubElement(note, "type").text = type_name
                if dotted:
                    SubElement(note, "dot")
                if piece.get("tie_stop"):
                    SubElement(note, "tie", type="stop")
                if piece.get("tie_start"):
                    SubElement(note, "tie", type="start")
                notations = None
                if piece.get("tie_start") or piece.get("tie_stop"):
                    notations = SubElement(note, "notations")
                    if piece.get("tie_stop"):
                        SubElement(notations, "tied", type="stop")
                    if piece.get("tie_start"):
                        SubElement(notations, "tied", type="start")
                if not piece["rest"] and piece.get("show_lyric") and piece["event"].articulation != "normal":
                    if notations is None:
                        notations = SubElement(note, "notations")
                    articulations = SubElement(notations, "articulations")
                    if piece["event"].articulation == "staccato":
                        SubElement(articulations, "staccato")
                    elif piece["event"].articulation == "tenuto":
                        SubElement(articulations, "tenuto")
                    elif piece["event"].articulation == "legato":
                        SubElement(articulations, "tenuto")
                if not piece["rest"] and piece.get("show_lyric") and voice_id == "V1_melody":
                    lyric = SubElement(note, "lyric", number="1")
                    SubElement(lyric, "text").text = piece["event"].symbol
            if number == len(contents_by_measure):
                barline = SubElement(measure, "barline", location="right")
                SubElement(barline, "bar-style").text = "light-heavy"

    indent(score, space="  ")
    stream = io.BytesIO()
    ElementTree(score).write(stream, encoding="utf-8", xml_declaration=True)
    return stream.getvalue()


def _vlq(value: int) -> bytes:
    value = max(0, int(value))
    buffer = value & 0x7F
    out = bytearray([buffer])
    while value >> 7:
        value >>= 7
        out.insert(0, (value & 0x7F) | 0x80)
    return bytes(out)


def _track_chunk(messages: list[tuple[int, int, bytes]]) -> bytes:
    messages.sort(key=lambda item: (item[0], item[1]))
    track = bytearray()
    previous = 0
    for tick, _, payload in messages:
        track.extend(_vlq(tick - previous))
        track.extend(payload)
        previous = tick
    track.extend(b"\x00\xff\x2f\x00")
    return b"MTrk" + struct.pack(">I", len(track)) + bytes(track)


def events_to_midi(
    events: list[MusicEvent],
    tempo: int,
    ticks_per_quarter: int = 480,
    codec_metadata: dict | None = None,
    sequence_metadata: dict | None = None,
) -> bytes:
    microseconds = int(60_000_000 / max(1, tempo))
    tempo_messages = [(0, 0, b"\xff\x51\x03" + microseconds.to_bytes(3, "big"))]
    if codec_metadata:
        payload = ("BIOSOUND_CODEC:" + compact_codec_json(codec_metadata)).encode("ascii")
        tempo_messages.append((0, 1, b"\xff\x01" + _vlq(len(payload)) + payload))
    if sequence_metadata:
        payload = ("BIOSOUND_SEQUENCE:" + compact_codec_json(sequence_metadata)).encode("ascii")
        tempo_messages.append((0, 2, b"\xff\x01" + _vlq(len(payload)) + payload))
    tracks = [_track_chunk(tempo_messages)]
    for voice_index, (voice_id, voice_events) in enumerate(_voice_groups(events)):
        channel = voice_index if voice_index < 9 else voice_index + 1
        timbre = voice_events[0].timbre
        name = VOICE_LABELS.get(voice_id, voice_id).encode("utf-8")
        messages: list[tuple[int, int, bytes]] = [
            (0, 0, b"\xff\x03" + _vlq(len(name)) + name),
            (0, 1, bytes([0xC0 | channel, ORCHESTRAL_PROGRAMS.get(timbre, ORCHESTRAL_PROGRAMS["flute"])])),
        ]
        for event in voice_events:
            start = int(round(event.onset * ticks_per_quarter))
            sounding_duration = max(0.03, event.duration * float(event.gate_ratio))
            end = int(round((event.onset + sounding_duration) * ticks_per_quarter))
            controls = dict(event.cc_controls)
            controls.setdefault(10, max(0, min(127, int(round((event.pan + 1.0) * 63.5)))))
            for control_index, (controller, value) in enumerate(sorted(controls.items())):
                messages.append((start, 2 + control_index, bytes([0xB0 | channel, int(controller), max(0, min(127, int(value)))])))
            messages.append((start, 8, bytes([0x90 | channel, event.midi, event.velocity])))
            messages.append((end, 0, bytes([0x80 | channel, event.midi, 0])))
        tracks.append(_track_chunk(messages))
    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), ticks_per_quarter)
    return header + b"".join(tracks)


def report_to_json(report: GVRReport, metadata: dict) -> bytes:
    payload = {"metadata": metadata, "gvr": report.to_dict()}
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def find_musescore() -> Path | None:
    candidates = [
        Path(r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe"),
        Path(r"C:\Program Files\MuseScore 3\bin\MuseScore3.exe"),
    ]
    command = shutil.which("MuseScore4") or shutil.which("mscore")
    if command:
        candidates.insert(0, Path(command))
    return next((p for p in candidates if p.exists()), None)


def musicxml_to_pdf(musicxml: bytes, timeout: int = 60) -> tuple[bytes | None, str]:
    executable = find_musescore()
    if not executable:
        return None, "未检测到 MuseScore；MusicXML 可下载后手动打开并导出 PDF。"
    with tempfile.TemporaryDirectory(prefix="biosound_score_") as folder:
        source = Path(folder) / "score.musicxml"
        target = Path(folder) / "score.pdf"
        source.write_bytes(musicxml)
        try:
            completed = subprocess.run(
                [str(executable), "-o", str(target), str(source)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, f"MuseScore 导出失败：{exc}"
        if completed.returncode == 0 and target.exists():
            return target.read_bytes(), "PDF 已由本机 MuseScore 生成。"
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        return None, f"MuseScore 未能生成 PDF（退出码 {completed.returncode}）：{detail[-300:]}"

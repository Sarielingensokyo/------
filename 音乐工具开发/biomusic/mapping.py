from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from .codec import encode_sequence, transform_row
from .models import BioRecord, MusicEvent


SCALES = {
    "五声音阶": [0, 2, 4, 7, 9],
    "多利亚调式": [0, 2, 3, 5, 7, 9, 10],
    "自然小调": [0, 2, 3, 5, 7, 8, 10],
    "半音阶": list(range(12)),
}

CLASSICAL_TIMBRES = {
    "flute", "oboe", "clarinet", "bassoon", "french_horn",
    "violin", "viola", "cello", "orchestral_harp",
}

VOICE_ORDER = {
    "V1_melody": 0,
    "V2_counterpoint": 1,
    "V3_bass": 2,
    "V4_horn_harmony": 3,
    "V5_viola_harmony": 4,
    "V6_harp_accents": 5,
}

VOICE_RANGES = {
    "V1_melody": (60, 92),
    "V2_counterpoint": (55, 88),
    "V3_bass": (36, 60),
    "V4_horn_harmony": (45, 72),
    "V5_viola_harmony": (48, 76),
    "V6_harp_accents": (48, 96),
}


@dataclass
class MappingSettings:
    pitch_mode: str = "文献氨基酸映射"
    scale_name: str = "多利亚调式"
    root_midi: int = 60
    tempo: int = 96
    meter_beats: int = 4
    meter_beat_type: int = 4
    max_events: int = 360
    min_midi: int = 36
    max_midi: int = 96
    row_form: str = "P"
    seed: int = 42
    breath_every: int = 24
    texture_density: int = 6
    counterpoint_strength: float = 0.7


def load_pitch_mapping(path: str | Path) -> dict[str, int]:
    mapping: dict[str, int] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            mapping[row["symbol"].upper()] = int(row["midi"])
    return mapping


def _nearest_midi(pc: int, target: float, low: int, high: int) -> int:
    candidates = [m for m in range(low, high + 1) if m % 12 == pc]
    if candidates:
        return min(candidates, key=lambda m: abs(m - target))
    return int(np.clip(round(target), low, high))


def _voice_midi(pc: int, target: float, voice_id: str) -> int:
    low, high = VOICE_RANGES[voice_id]
    return _nearest_midi(pc % 12, target, low, high)


def _feature(record: BioRecord, name: str, index: int, default: float = 0.0) -> float:
    values = record.features.get(name)
    return float(values[index]) if values and index < len(values) else default


def _duration(record: BioRecord, index: int) -> float:
    secondary = record.categories.get("secondary_structure", [])
    if index < len(secondary):
        return {"helix": 1.0, "sheet": 1.5, "coil": 0.5}.get(secondary[index], 0.5)
    if record.data_type == "protein":
        hydro = _feature(record, "hydropathy_normalized", index, 0.5)
        return 0.5 if hydro > 0.62 else (1.0 if hydro < 0.35 else 0.75)
    if record.data_type in {"omics", "transcriptomics"}:
        return 0.5 + _feature(record, "detected_features", index, 0.5)
    if record.data_type in {"epigenomics", "metabolomics", "association", "mass_spectrometry"}:
        return 0.5 + 0.75 * _feature(record, "value", index, 0.5)
    return 0.5


def _melody_timbre(record: BioRecord) -> str:
    return {
        "protein": "oboe",
        "dna": "flute",
        "rna": "clarinet",
        "omics": "clarinet",
        "transcriptomics": "clarinet",
        "epigenomics": "viola",
        "mass_spectrometry": "orchestral_harp",
        "metabolomics": "orchestral_harp",
        "association": "french_horn",
    }.get(record.data_type, "flute")


def _base_events(
    record: BioRecord,
    settings: MappingSettings,
    pitch_map: dict[str, int],
    tone_row: list[int] | None,
) -> list[MusicEvent]:
    stride = max(1, int(np.ceil(record.length / settings.max_events)))
    indices = list(range(0, record.length, stride))
    scale = SCALES.get(settings.scale_name, SCALES["多利亚调式"])
    events: list[MusicEvent] = []
    onset = 0.0
    melody_timbre = _melody_timbre(record)

    for local_id, index in enumerate(indices):
        symbol = record.symbols[index].upper()
        hydro = _feature(record, "hydropathy_normalized", index, _feature(record, "value", index, 0.5))
        charge = _feature(record, "charge", index, _feature(record, "effect", index, 0.0))
        contact = _feature(record, "contact_degree", index, 0.5)
        value = _feature(record, "value", index, hydro)
        uncertainty = _feature(record, "uncertainty", index, 0.0)

        if tone_row is not None:
            row_position = local_id % 12
            pc = tone_row[row_position]
            target = settings.root_midi + 12 * (0.5 + 0.8 * charge + 0.4 * (contact - 0.5))
            midi = _voice_midi(pc, target, "V1_melody")
            rule = f"{settings.row_form} 音列[{row_position}]={pc}；生物特征控制音区、时值、力度和空间"
        elif settings.pitch_mode == "文献氨基酸映射" and record.data_type == "protein":
            raw = pitch_map.get(symbol, settings.root_midi)
            pc = raw % 12
            midi = _voice_midi(pc, raw + 12 * int(round(charge)), "V1_melody")
            row_position = None
            rule = f"pitch_mapping.csv[{symbol}] + 电荷控制八度"
        elif record.data_type in {"dna", "rna"}:
            degree = {"A": 0, "C": 1, "G": 2, "T": 3, "U": 3, "N": 4}.get(symbol, 0)
            pc = (settings.root_midi + scale[degree % len(scale)]) % 12
            midi = _voice_midi(pc, settings.root_midi + 12 * _feature(record, "gc", index, 0), "V1_melody")
            row_position = None
            rule = "碱基身份→调式音级；GC/嘌呤特征→音区和力度"
        elif record.data_type == "mass_spectrometry":
            mz = _feature(record, "mz", index, 0.0)
            pc = int(round(mz)) % 12
            midi = _voice_midi(pc, settings.root_midi + 18 * value, "V1_melody")
            row_position = None
            rule = "m/z 模 12→音级；峰强度→力度；置信度→清晰度"
        else:
            degree = int(round(value * (len(scale) - 1)))
            pc = (settings.root_midi + scale[degree]) % 12
            midi = _voice_midi(pc, settings.root_midi + 12 * (contact - 0.5), "V1_melody")
            row_position = None
            rule = f"{record.data_type} 主特征→调式音级；结构/数值→音区"

        duration = max(0.25, round(_duration(record, index) * 4) / 4)
        biological_pan = _feature(record, "spatial_pan", index, 0.0)
        pan = float(np.clip(-0.12 + 0.55 * biological_pan, -1, 1))
        if record.data_type in {"omics", "transcriptomics"}:
            mito = _feature(record, "mitochondrial_fraction", index, 0.0)
            hvg = _feature(record, "hvg_score", index, 0.5)
            velocity = int(np.clip(48 + 68 * hvg - 35 * mito, 25, 118))
        else:
            velocity = int(np.clip(60 + 28 * abs(charge) + 18 * value - 18 * uncertainty, 34, 118))

        events.append(MusicEvent(
            event_id=local_id,
            source_index=index,
            source_label=record.source_labels[index],
            symbol=symbol,
            onset=round(onset, 4),
            duration=duration,
            midi=midi,
            velocity=velocity,
            pan=pan,
            timbre=melody_timbre,
            expected_pc=pc,
            mapping_rule=rule,
            voice_id="V1_melody",
            role="foreground_melody",
            parent_event_id=None,
            features={
                "hydropathy": round(hydro, 4),
                "charge": round(charge, 4),
                "contact_degree": round(contact, 4),
                "value": round(value, 4),
                "uncertainty": round(uncertainty, 4),
            },
            row_position=row_position,
            row_form=settings.row_form if tone_row is not None else None,
        ))
        onset += duration
        if settings.breath_every and (local_id + 1) % settings.breath_every == 0:
            onset += 0.25
    return events


def _codec_events(
    record: BioRecord,
    settings: MappingSettings,
    tone_rows: list[list[int]],
    codec: dict,
) -> list[MusicEvent]:
    """Create the complete, non-downsampled V1 carrier timeline."""
    block_size = int(codec["block_size"])
    events: list[MusicEvent] = []
    onset = 0.0
    melody_timbre = _melody_timbre(record)
    event_id = 0
    for block_index, row in enumerate(tone_rows):
        for row_position, pc in enumerate(row):
            # DNA/RNA: one carrier note per base. Protein: two notes per residue.
            source_offset = row_position if block_size == 12 else row_position // 2
            source_index = block_index * block_size + source_offset
            is_padding = source_index >= record.length
            if is_padding:
                source_label = f"PAD:{source_index + 1}"
                symbol = str(codec["pad_symbol"])
                feature_index = max(0, record.length - 1)
            else:
                source_label = record.source_labels[source_index]
                symbol = record.symbols[source_index].upper()
                feature_index = source_index

            hydro = _feature(record, "hydropathy_normalized", feature_index, _feature(record, "value", feature_index, 0.5))
            charge = _feature(record, "charge", feature_index, _feature(record, "effect", feature_index, 0.0))
            contact = _feature(record, "contact_degree", feature_index, 0.5)
            value = _feature(record, "value", feature_index, hydro)
            uncertainty = _feature(record, "uncertainty", feature_index, 0.0)
            target = settings.root_midi + 12 * (0.5 + 0.8 * charge + 0.4 * (contact - 0.5))
            midi = _voice_midi(pc, target, "V1_melody")
            duration_scale = 0.5 if block_size == 6 else 1.0
            duration = max(0.25, round(_duration(record, feature_index) * duration_scale * 4) / 4)
            biological_pan = _feature(record, "spatial_pan", feature_index, 0.0)
            pan = float(np.clip(-0.12 + 0.55 * biological_pan, -1, 1))
            velocity = int(np.clip(60 + 28 * abs(charge) + 18 * value - 18 * uncertainty, 34, 118))
            rule = (
                f"可逆载体块 {block_index + 1}/{len(tone_rows)}，{settings.row_form}[{row_position}]={pc}；"
                f"音级承载序列，音区/时值/力度/声像由来源位置 {source_label} 的生物特征控制"
            )
            events.append(MusicEvent(
                event_id=event_id,
                source_index=source_index,
                source_label=source_label,
                symbol=symbol,
                onset=round(onset, 4),
                duration=duration,
                midi=midi,
                velocity=velocity,
                pan=pan,
                timbre=melody_timbre,
                expected_pc=pc,
                mapping_rule=rule,
                voice_id="V1_melody",
                role="lossless_codec_carrier",
                parent_event_id=None,
                features={
                    "hydropathy": round(hydro, 4),
                    "charge": round(charge, 4),
                    "contact_degree": round(contact, 4),
                    "value": round(value, 4),
                    "uncertainty": round(uncertainty, 4),
                },
                row_position=row_position,
                row_form=settings.row_form,
                codec_block=block_index,
                is_codec_carrier=True,
            ))
            event_id += 1
            onset += duration
        # Block boundaries are audible but do not add or remove carrier notes.
        onset += 0.25
    return events


def _derived(
    parent: MusicEvent,
    *,
    voice_id: str,
    role: str,
    timbre: str,
    onset: float,
    duration: float,
    midi: int,
    velocity: int,
    pan: float,
    rule: str,
    row_form: str | None = None,
) -> MusicEvent:
    return replace(
        parent,
        event_id=-1,
        onset=round(onset, 4),
        duration=max(0.25, round(duration * 4) / 4),
        midi=midi,
        velocity=int(np.clip(velocity, 1, 127)),
        pan=float(np.clip(pan, -1, 1)),
        timbre=timbre,
        expected_pc=midi % 12,
        mapping_rule=rule,
        voice_id=voice_id,
        role=role,
        parent_event_id=parent.event_id,
        row_form=row_form,
        codec_block=None,
        is_codec_carrier=False,
        status="proposed",
    )


def _orchestrate(
    melody: list[MusicEvent],
    record: BioRecord,
    settings: MappingSettings,
    tone_rows: list[list[int]] | None,
) -> list[MusicEvent]:
    if not melody:
        return []
    events = list(melody)
    density = int(np.clip(settings.texture_density, 1, 6))
    total_end = max(e.onset + e.duration for e in melody)

    if density >= 2:
        step = 2 if settings.counterpoint_strength >= 0.55 else 3
        next_onset = 0.0
        for i, parent in enumerate(melody[1::step]):
            onset = max(parent.onset + 0.5, next_onset)
            if onset >= total_end:
                continue
            if tone_rows and parent.codec_block is not None and parent.row_position is not None:
                inverted_row = transform_row(tone_rows[parent.codec_block], "I")
                pc = inverted_row[parent.row_position]
                target = 72 - (parent.midi - 66) * settings.counterpoint_strength
                midi = _voice_midi(pc, target, "V2_counterpoint")
                rule = "由主旋律来源派生；I 音列保持音级，轮唱延迟与反向音区形成对位"
                row_form = "I"
            else:
                contour = parent.midi - melody[max(0, parent.parent_event_id or 0)].midi if parent.parent_event_id else 0
                interval = 3 if i % 2 == 0 else 4
                pc = (parent.midi - interval - int(np.sign(contour))) % 12
                midi = _voice_midi(pc, 70 - (parent.midi - 66) * settings.counterpoint_strength, "V2_counterpoint")
                rule = "由同一生物事件延迟派生；小/大三度与反向音区形成可辨识对位"
                row_form = None
            duration = min(1.5, max(0.5, parent.duration + 0.25))
            duration = min(duration, max(0.25, total_end - onset))
            events.append(_derived(
                parent, voice_id="V2_counterpoint", role="derived_counterpoint", timbre="violin",
                onset=onset, duration=duration, midi=midi, velocity=parent.velocity - 12,
                pan=0.28 + 0.25 * parent.pan, rule=rule, row_form=row_form,
            ))
            next_onset = onset + duration

    group = max(4, settings.meter_beats * 2)
    anchors = melody[::group]
    if density >= 3:
        for i, parent in enumerate(anchors):
            end = anchors[i + 1].onset if i + 1 < len(anchors) else total_end
            pc = (settings.root_midi + (0 if i % 2 == 0 else 7)) % 12
            midi = _voice_midi(pc, 45 + 5 * parent.features.get("value", 0.5), "V3_bass")
            events.append(_derived(
                parent, voice_id="V3_bass", role="structural_bass", timbre="cello",
                onset=parent.onset, duration=max(0.5, end - parent.onset), midi=midi,
                velocity=parent.velocity - 16, pan=-0.18, rule="序列/样本分组边界→持续低音；组值决定主音或属音",
            ))

    pad_group = group * 2
    pads = melody[::pad_group]
    if density >= 4:
        for i, parent in enumerate(pads):
            end = pads[i + 1].onset if i + 1 < len(pads) else total_end
            root_pc = (settings.root_midi + (5 if i % 3 == 1 else 0)) % 12
            midi = _voice_midi(root_pc, 55 + 8 * parent.features.get("contact_degree", 0.5), "V4_horn_harmony")
            events.append(_derived(
                parent, voice_id="V4_horn_harmony", role="structural_harmony", timbre="french_horn",
                onset=parent.onset, duration=max(1.0, end - parent.onset), midi=midi,
                velocity=parent.velocity - 22, pan=-0.42, rule="结构/功能区块→圆号持续和声；接触度→音区张力",
            ))

    if density >= 5:
        for i, parent in enumerate(pads):
            end = pads[i + 1].onset if i + 1 < len(pads) else total_end
            third = 3 if settings.scale_name in {"自然小调", "多利亚调式"} else 4
            pc = (settings.root_midi + third + (2 if i % 3 == 2 else 0)) % 12
            midi = _voice_midi(pc, 62 + 5 * parent.features.get("hydropathy", 0.5), "V5_viola_harmony")
            events.append(_derived(
                parent, voice_id="V5_viola_harmony", role="inner_harmony", timbre="viola",
                onset=parent.onset, duration=max(1.0, end - parent.onset), midi=midi,
                velocity=parent.velocity - 27, pan=0.42, rule="与圆号区块同步的中声部；理化特征控制三度色彩与音区",
            ))

    if density >= 6:
        last_harp_end = -1.0
        for i, parent in enumerate(melody):
            structural_change = i == 0 or (
                record.categories.get("secondary_structure")
                and parent.source_index > 0
                and record.categories["secondary_structure"][parent.source_index]
                != record.categories["secondary_structure"][parent.source_index - 1]
            )
            salient = parent.features.get("contact_degree", 0.0) >= 0.82 or parent.features.get("value", 0.0) >= 0.92
            phrase = settings.breath_every and i % settings.breath_every == 0
            if not (structural_change or salient or phrase) or parent.onset < last_harp_end:
                continue
            pcs = [parent.midi % 12, (parent.midi + 4) % 12, (parent.midi + 7) % 12]
            for j, pc in enumerate(pcs):
                onset = parent.onset + 0.25 * j
                midi = _voice_midi(pc, 72 + 5 * j, "V6_harp_accents")
                events.append(_derived(
                    parent, voice_id="V6_harp_accents", role="structural_accent", timbre="orchestral_harp",
                    onset=onset, duration=0.25, midi=midi, velocity=parent.velocity - 5 - 5 * j,
                    pan=0.08 + 0.18 * j, rule="结构边界、接触峰或高显著性位置→竖琴三音结构标记",
                ))
            last_harp_end = parent.onset + 1.0

    events.sort(key=lambda e: (e.onset, VOICE_ORDER.get(e.voice_id, 99), e.parent_event_id or -1, e.midi))
    melody_id_map = {
        event.event_id: new_id
        for new_id, event in enumerate(events)
        if event.voice_id == "V1_melody"
    }
    for event in events:
        if event.parent_event_id is not None:
            event.parent_event_id = melody_id_map.get(event.parent_event_id)
    for new_id, event in enumerate(events):
        event.event_id = new_id
    return events


def generate_events(
    record: BioRecord,
    settings: MappingSettings,
    pitch_map: dict[str, int],
) -> tuple[list[MusicEvent], list[list[int]] | None, dict | None]:
    if not record.symbols:
        return [], None, None
    codec_mode = settings.pitch_mode in {"十二音列 GVR", "可逆十二音列编解码"}
    if codec_mode:
        tone_rows, codec = encode_sequence("".join(record.symbols), record.data_type, settings.row_form)
        melody = _codec_events(record, settings, tone_rows, codec)
        return _orchestrate(melody, record, settings, tone_rows), tone_rows, codec
    melody = _base_events(record, settings, pitch_map, None)
    return _orchestrate(melody, record, settings, None), None, None

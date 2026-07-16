from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .exporters import events_to_midi, events_to_musicxml, report_to_json
from .features import compute_coarse_nma, enrich_record
from .gvr import repair_events
from .mapping import MappingSettings, generate_events, load_pitch_mapping
from .models import BioRecord, GVRReport, MusicEvent
from .parsers import parse_uploaded
from .synth import render_wav


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PITCH_MAP = PACKAGE_ROOT / "config" / "pitch_mapping.csv"


@dataclass
class SonificationSettings:
    forced_type: str = "auto"
    record_index: int = 0
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
    enable_nma: bool = True
    nma_cutoff: float = 12.0
    max_audio_seconds: float = 150.0
    texture_density: int = 6
    counterpoint_strength: float = 0.7


@dataclass
class PipelineResult:
    record: BioRecord
    all_record_names: list[str]
    events: list[MusicEvent]
    report: GVRReport
    nma: dict
    audio_info: dict
    wav: bytes
    midi: bytes
    musicxml: bytes
    trace_csv: bytes
    report_json: bytes
    summary: dict


def _trace_csv(events: list[MusicEvent]) -> bytes:
    stream = io.StringIO()
    fields = [
        "event_id", "voice_id", "role", "parent_event_id", "source_index", "source_label", "symbol", "onset_quarter",
        "duration_quarter", "midi", "pitch_class", "velocity", "pan", "timbre",
        "expected_pc", "row_position", "row_form", "status", "mapping_rule",
        "codec_block", "is_codec_carrier",
        "hydropathy", "charge", "contact_degree", "value", "uncertainty",
    ]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for event in events:
        row = event.to_dict()
        writer.writerow({
            "event_id": event.event_id,
            "voice_id": event.voice_id,
            "role": event.role,
            "parent_event_id": event.parent_event_id if event.parent_event_id is not None else "",
            "source_index": event.source_index,
            "source_label": event.source_label,
            "symbol": event.symbol,
            "onset_quarter": event.onset,
            "duration_quarter": event.duration,
            "midi": event.midi,
            "pitch_class": row["pitch_class"],
            "velocity": event.velocity,
            "pan": round(event.pan, 4),
            "timbre": event.timbre,
            "expected_pc": event.expected_pc,
            "row_position": event.row_position if event.row_position is not None else "",
            "row_form": event.row_form or "",
            "codec_block": event.codec_block if event.codec_block is not None else "",
            "is_codec_carrier": event.is_codec_carrier,
            "status": event.status,
            "mapping_rule": event.mapping_rule,
            **event.features,
        })
    return stream.getvalue().encode("utf-8-sig")


def run_pipeline(filename: str, data: bytes, settings: SonificationSettings, pitch_map_path: str | Path = DEFAULT_PITCH_MAP) -> PipelineResult:
    records = parse_uploaded(filename, data, settings.forced_type)
    if not records:
        raise ValueError("没有得到可处理记录。")
    index = max(0, min(settings.record_index, len(records) - 1))
    record = enrich_record(records[index])
    nma = compute_coarse_nma(record, cutoff=settings.nma_cutoff) if settings.enable_nma and record.coordinates else {"available": False, "reason": "当前输入没有 PDB 三维坐标或 NMA 已关闭。"}
    mapping_settings = MappingSettings(
        pitch_mode=settings.pitch_mode,
        scale_name=settings.scale_name,
        root_midi=settings.root_midi,
        tempo=settings.tempo,
        meter_beats=settings.meter_beats,
        meter_beat_type=settings.meter_beat_type,
        max_events=settings.max_events,
        min_midi=settings.min_midi,
        max_midi=settings.max_midi,
        row_form=settings.row_form,
        seed=settings.seed,
        breath_every=settings.breath_every,
        texture_density=settings.texture_density,
        counterpoint_strength=settings.counterpoint_strength,
    )
    pitch_map = load_pitch_mapping(pitch_map_path)
    proposed, tone_rows, codec = generate_events(record, mapping_settings, pitch_map)
    events, report = repair_events(proposed, settings.min_midi, settings.max_midi, tone_rows, codec)
    if not report.passed:
        details = "; ".join(v.message for v in report.violations_after[:4])
        raise ValueError(f"GVR 最终检查未通过，未发布音频：{details}")

    mito_values = record.features.get("mitochondrial_fraction", [0.0])
    mean_mito = float(np.mean(mito_values)) if mito_values else 0.0
    wav, audio_info = render_wav(
        events,
        settings.tempo,
        nma=nma,
        qc_mito_fraction=mean_mito,
        max_seconds=settings.max_audio_seconds,
        seed=settings.seed,
    )
    title = f"{record.name} - BioSound GVR"
    musicxml = events_to_musicxml(
        events, title, settings.tempo, (settings.meter_beats, settings.meter_beat_type), codec_metadata=codec
    )
    midi = events_to_midi(events, settings.tempo, codec_metadata=codec)
    summary = {
        "record_name": record.name,
        "data_type": record.data_type,
        "source_items": record.length,
        "musical_events": len(events),
        "voice_count": len({event.voice_id for event in events}),
        "voices": {
            voice_id: {
                "events": sum(1 for event in events if event.voice_id == voice_id),
                "instrument": next(event.timbre for event in events if event.voice_id == voice_id),
                "role": next(event.role for event in events if event.voice_id == voice_id),
            }
            for voice_id in sorted({event.voice_id for event in events})
        },
        "downsample_stride": 1 if codec else max(1, int(np.ceil(record.length / settings.max_events))),
        "pitch_mode": settings.pitch_mode,
        "tempo": settings.tempo,
        "meter": f"{settings.meter_beats}/{settings.meter_beat_type}",
        "gvr_passed": report.passed,
        "repairs": len(report.repairs),
        "mean_mitochondrial_fraction": round(mean_mito, 5),
        "nma_available": bool(nma.get("available")),
        "texture_density": settings.texture_density,
        "lossless_codec": bool(codec),
        "codec_blocks": int(codec["block_count"]) if codec else 0,
        "codec_version": codec["codec_version"] if codec else None,
        "scientific_scope": "可追溯的规则与相对物理特征声学化；不是分子真实声波的直接录音。",
    }
    metadata = {**summary, "record_metadata": record.metadata, "audio": audio_info, "nma": nma}
    return PipelineResult(
        record=record,
        all_record_names=[r.name for r in records],
        events=events,
        report=report,
        nma=nma,
        audio_info=audio_info,
        wav=wav,
        midi=midi,
        musicxml=musicxml,
        trace_csv=_trace_csv(events),
        report_json=report_to_json(report, metadata),
        summary=summary,
    )

from __future__ import annotations

import json
from pathlib import Path

from biomusic.exporters import musicxml_to_pdf
from biomusic.pipeline import SonificationSettings, run_pipeline


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "examples" / "example_expression.csv"
OUTPUT = ROOT / "generated_works" / "细胞脉冲_SF2三音色版"
STEM = "细胞脉冲_SF2三音色版"


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    settings = SonificationSettings(
        pitch_mode="生物物理调式映射（推荐）",
        scale_name="多利亚调式",
        tempo=92,
        meter_beats=4,
        meter_beat_type=4,
        max_events=180,
        texture_density=6,
        counterpoint_strength=0.72,
        max_audio_seconds=90,
        seed=91,
        audio_backend="soundfont",
    )
    result = run_pipeline(SOURCE.name, SOURCE.read_bytes(), settings)
    for suffix, content in {
        ".wav": result.wav,
        ".mid": result.midi,
        ".musicxml": result.musicxml,
        "_trace.csv": result.trace_csv,
        "_gvr.json": result.report_json,
    }.items():
        (OUTPUT / f"{STEM}{suffix}").write_bytes(content)

    pdf, pdf_message = musicxml_to_pdf(result.musicxml)
    if pdf:
        (OUTPUT / f"{STEM}.pdf").write_bytes(pdf)
    manifest = {
        "title": STEM,
        "source": str(SOURCE.relative_to(ROOT)),
        "audio_backend": result.audio_info.get("audio_backend"),
        "sample_rate": result.audio_info.get("sample_rate"),
        "soundfont_assignments": result.audio_info.get("soundfont_assignments"),
        "qc_lowpass_cutoff_hz": result.audio_info.get("qc_lowpass_cutoff_hz"),
        "pdf_export": pdf_message,
    }
    (OUTPUT / f"{STEM}_音源清单.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

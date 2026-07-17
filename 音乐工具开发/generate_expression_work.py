from __future__ import annotations

import json
from pathlib import Path

from biomusic.codec import decode_artifact
from biomusic.exporters import musicxml_to_pdf
from biomusic.pipeline import SonificationSettings, run_pipeline


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "examples" / "example_structure.pdb"
OUTPUT = ROOT / "generated_works" / "结构呼吸_利底亚版"
STEM = "结构呼吸_利底亚版"


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    settings = SonificationSettings(
        forced_type="pdb",
        pitch_mode="生物物理调式映射（推荐）",
        scale_name="利底亚调式",
        root_midi=60,
        tempo=84,
        meter_beats=6,
        meter_beat_type=8,
        max_events=240,
        texture_density=6,
        counterpoint_strength=0.78,
        enable_nma=True,
        max_audio_seconds=120.0,
        seed=73,
    )
    result = run_pipeline(SOURCE.name, SOURCE.read_bytes(), settings)

    artifacts = {
        ".wav": result.wav,
        ".mid": result.midi,
        ".musicxml": result.musicxml,
        "_trace.csv": result.trace_csv,
        "_gvr.json": result.report_json,
    }
    written: list[str] = []
    for suffix, content in artifacts.items():
        target = OUTPUT / f"{STEM}{suffix}"
        target.write_bytes(content)
        written.append(target.name)

    recovered = {}
    for suffix in (".mid", ".musicxml", "_gvr.json"):
        path = OUTPUT / f"{STEM}{suffix}"
        sequence, metadata, rows = decode_artifact(path.name, path.read_bytes())
        recovered[suffix] = {
            "sequence_matches_source": sequence == "".join(result.record.symbols),
            "recovered_length": len(sequence),
            "pitch_rows_used_for_recovery": len(rows),
            "payload_version": metadata.get("payload_version"),
        }

    pdf, pdf_message = musicxml_to_pdf(result.musicxml)
    if pdf:
        pdf_path = OUTPUT / f"{STEM}.pdf"
        pdf_path.write_bytes(pdf)
        written.append(pdf_path.name)

    manifest = {
        "title": STEM,
        "source": str(SOURCE.relative_to(ROOT)),
        "settings": {
            "pitch_mode": settings.pitch_mode,
            "scale": settings.scale_name,
            "root_midi": settings.root_midi,
            "tempo": settings.tempo,
            "meter": f"{settings.meter_beats}/{settings.meter_beat_type}",
            "texture_density": settings.texture_density,
            "counterpoint_strength": settings.counterpoint_strength,
            "nma_enabled": settings.enable_nma,
        },
        "summary": result.summary,
        "feature_sources": {
            "sasa": result.record.metadata.get("sasa_source"),
            "dihedral": result.record.metadata.get("dihedral_source"),
            "b_factor": result.record.metadata.get("b_factor_source"),
            "secondary_structure": result.record.metadata.get("secondary_structure_source"),
        },
        "information_fidelity": {
            "description": "推荐映射不把全部信息硬塞进裸音高；原始规范化序列以带 SHA-256 校验的符号载荷写入完整产物。",
            "artifact_decode_checks": recovered,
        },
        "scientific_scope": "示例 PDB 只有 CA 原子，因此 rSASA 使用接触密度暴露代理，主链刚度使用 HELIX/SHEET/coil 基线；上传全原子 PDB 后才启用轻量 Shrake-Rupley rSASA 与 phi/psi 连续变化。",
        "pdf_export": pdf_message,
        "files": sorted(written),
    }
    manifest_path = OUTPUT / f"{STEM}_说明与校验.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

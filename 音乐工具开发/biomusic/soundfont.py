from __future__ import annotations

import io
import math
import warnings
import wave
from pathlib import Path

import numpy as np

from .exporters import ORCHESTRAL_PROGRAMS
from .models import MusicEvent


ROOT = Path(__file__).resolve().parent.parent
SOUNDFONT_ROOT = ROOT / "assets" / "soundfonts" / "banks"
TIM_GM = SOUNDFONT_ROOT / "TimGM.sf2"
CLARINET = SOUNDFONT_ROOT / "Clarinet-SF2-20190818" / "Clarinet-20190818.sf2"
HARP = SOUNDFONT_ROOT / "ConcertHarp-small-SF2-20200702" / "ConcertHarp-small-20200702.sf2"

SOUNDFONT_FILES = {
    "TimGM6mb": TIM_GM,
    "FreePats Clarinet": CLARINET,
    "FreePats Concert Harp": HARP,
}


class SoundFontUnavailable(RuntimeError):
    pass


def validate_soundfonts() -> dict[str, dict]:
    status: dict[str, dict] = {}
    for name, path in SOUNDFONT_FILES.items():
        valid = False
        error = ""
        size = 0
        try:
            size = path.stat().st_size
            header = path.read_bytes()[:12]
            valid = size > 1024 and header[:4] == b"RIFF" and header[8:12] == b"sfbk"
            if not valid:
                error = "文件不是有效的 RIFF SoundFont 2 音色库"
        except OSError as exc:
            error = str(exc)
        status[name] = {
            "path": str(path.relative_to(ROOT)),
            "size_bytes": size,
            "size_mib": round(size / 1024 / 1024, 2),
            "valid": valid,
            "error": error,
        }
    return status


def _moving_average(signal: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return signal
    kernel = np.ones(window, dtype=np.float32) / window
    return np.column_stack([
        np.convolve(signal[:, channel], kernel, mode="same") for channel in range(signal.shape[1])
    ]).astype(np.float32)


def _nma_midi(frequency: float, allowed_pitch_classes: set[int] | None = None) -> int:
    midi = int(round(69 + 12 * math.log2(max(1e-6, frequency) / 440.0)))
    while midi < 36:
        midi += 12
    while midi > 59:
        midi -= 12
    midi = int(np.clip(midi, 36, 59))
    if allowed_pitch_classes is not None:
        candidates = [m for m in range(36, 60) if m % 12 in allowed_pitch_classes]
        midi = min(candidates, key=lambda m: (abs(m - midi), m))
    return midi


def _load_fluidsynth():
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Couldn't find ffmpeg or avconv.*")
            from sf2_loader import fluidsynth
    except Exception as exc:  # pragma: no cover - environment-specific failure
        raise SoundFontUnavailable(f"无法加载 sf2-loader/FluidSynth：{exc}") from exc
    return fluidsynth


def render_soundfont_wav(
    events: list[MusicEvent],
    tempo: int,
    nma: dict | None = None,
    qc_mito_fraction: float = 0.0,
    max_seconds: float = 150.0,
    allowed_pitch_classes: set[int] | None = None,
) -> tuple[bytes, dict]:
    if not events:
        raise ValueError("没有可渲染事件。")
    status = validate_soundfonts()
    invalid = [name for name, info in status.items() if not info["valid"]]
    if invalid:
        raise SoundFontUnavailable("缺失或损坏的 SoundFont：" + "、".join(invalid))

    fluidsynth = _load_fluidsynth()
    sample_rate = 44100
    seconds_per_beat = 60.0 / max(1, tempo)
    requested_seconds = max(e.onset + e.duration for e in events) * seconds_per_beat + 0.9
    total_seconds = min(requested_seconds, max_seconds)
    total_frames = max(1, int(total_seconds * sample_rate))

    synth = fluidsynth.Synth(gain=0.34)
    try:
        sfids = {
            "TimGM6mb": synth.sfload(str(TIM_GM.resolve())),
            "FreePats Clarinet": synth.sfload(str(CLARINET.resolve())),
            "FreePats Concert Harp": synth.sfload(str(HARP.resolve())),
        }
        if any(sfid < 0 for sfid in sfids.values()):
            raise SoundFontUnavailable("FluidSynth 无法载入一个或多个 SF2 文件。")

        voice_ids = sorted({event.voice_id for event in events})
        if len(voice_ids) > 12:
            raise SoundFontUnavailable("当前采样器最多为 12 个作品声部分配独立通道。")
        channels = {voice_id: index for index, voice_id in enumerate(voice_ids)}
        assignments: dict[str, dict] = {}
        for voice_id, channel in channels.items():
            timbre = next(event.timbre for event in events if event.voice_id == voice_id)
            if timbre == "clarinet":
                bank_name, preset = "FreePats Clarinet", 0
            elif timbre == "orchestral_harp":
                bank_name, preset = "FreePats Concert Harp", 0
            else:
                bank_name = "TimGM6mb"
                preset = ORCHESTRAL_PROGRAMS.get(timbre, ORCHESTRAL_PROGRAMS["flute"])
            result = synth.program_select(channel, sfids[bank_name], 0, preset)
            if result != 0:
                raise SoundFontUnavailable(f"无法为 {voice_id} 选择 {bank_name} preset {preset}。")
            assignments[voice_id] = {
                "channel": channel,
                "timbre": timbre,
                "soundfont": bank_name,
                "bank": 0,
                "preset": preset,
            }

        commands: list[tuple[int, int, str, tuple[int, ...]]] = []
        rendered_events = 0
        for event in events:
            start = int(event.onset * seconds_per_beat * sample_rate)
            if start >= total_frames:
                continue
            sounding_beats = max(0.03, event.duration * float(event.gate_ratio))
            end = min(total_frames - 1, start + max(1, int(sounding_beats * seconds_per_beat * sample_rate)))
            channel = channels[event.voice_id]
            for cc in (1, 10, 74, 91, 93):
                value = int(np.clip(event.cc_controls.get(cc, 0), 0, 127))
                commands.append((start, 1, "cc", (channel, cc, value)))
            commands.append((start, 2, "noteon", (channel, int(event.midi), int(event.velocity))))
            commands.append((end, 0, "noteoff", (channel, int(event.midi))))
            rendered_events += 1

        nma_notes: list[int] = []
        if nma and nma.get("available"):
            nma_programs = [42, 60, 41]  # cello, horn, viola
            for mode_index, frequency in enumerate(nma.get("audible_frequencies_hz", [])[:3]):
                channel = 12 + mode_index
                preset = nma_programs[mode_index]
                synth.program_select(channel, sfids["TimGM6mb"], 0, preset)
                synth.cc(channel, 10, [45, 82, 64][mode_index])
                synth.cc(channel, 91, 58)
                synth.cc(channel, 93, 24)
                midi = _nma_midi(float(frequency), allowed_pitch_classes)
                nma_notes.append(midi)
                commands.append((0, 2, "noteon", (channel, midi, 17 - mode_index * 2)))
                commands.append((total_frames - 1, 0, "noteoff", (channel, midi)))

        commands.sort(key=lambda item: (item[0], item[1]))
        chunks: list[np.ndarray] = []
        cursor = 0
        index = 0
        while index < len(commands):
            frame = commands[index][0]
            if frame > cursor:
                chunks.append(np.asarray(synth.get_samples(frame - cursor), dtype=np.int16).reshape(-1, 2))
                cursor = frame
            while index < len(commands) and commands[index][0] == frame:
                _, _, command, args = commands[index]
                if command == "cc":
                    synth.cc(*args)
                elif command == "noteon":
                    synth.noteon(*args)
                else:
                    synth.noteoff(*args)
                index += 1
        if cursor < total_frames:
            chunks.append(np.asarray(synth.get_samples(total_frames - cursor), dtype=np.int16).reshape(-1, 2))
        pcm = np.concatenate(chunks, axis=0) if chunks else np.zeros((total_frames, 2), dtype=np.int16)
    finally:
        synth.delete()

    stereo = pcm.astype(np.float32) / 32768.0
    cutoff_hz = float(700 + (1.0 - np.clip(qc_mito_fraction, 0, 1)) * 9000)
    window = max(1, int(sample_rate / max(700.0, cutoff_hz)))
    stereo = _moving_average(stereo, window)
    peak = float(np.max(np.abs(stereo)))
    if peak > 0:
        stereo *= min(0.96 / peak, 1.0)
    pcm_out = (stereo * 32767).astype("<i2")
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_out.tobytes())
    return stream.getvalue(), {
        "duration_seconds": round(total_seconds, 3),
        "requested_duration_seconds": round(requested_seconds, 3),
        "truncated": requested_seconds > max_seconds,
        "rendered_events": rendered_events,
        "sample_rate": sample_rate,
        "channels": 2,
        "qc_lowpass_cutoff_hz": round(cutoff_hz, 1),
        "voice_count": len(channels),
        "voice_event_counts": {
            voice_id: sum(1 for event in events if event.voice_id == voice_id) for voice_id in voice_ids
        },
        "preview_source": "SoundFont 2 sample playback through bundled sf2-loader/FluidSynth",
        "audio_backend": "soundfont",
        "soundfont_status": status,
        "soundfont_assignments": assignments,
        "nma_sampled_background_notes": nma_notes,
        "nma_scale_quantized": allowed_pitch_classes is not None,
        "strict_scale_pitch_classes": sorted(allowed_pitch_classes) if allowed_pitch_classes is not None else None,
    }

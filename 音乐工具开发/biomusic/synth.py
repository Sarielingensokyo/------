from __future__ import annotations

import io
import math
import wave

import numpy as np

from .models import MusicEvent


def _adsr(length: int, sample_rate: int) -> np.ndarray:
    attack = min(length // 4, int(0.02 * sample_rate))
    release = min(length // 3, int(0.06 * sample_rate))
    envelope = np.ones(length, dtype=np.float32)
    if attack:
        envelope[:attack] = np.linspace(0, 1, attack, endpoint=False)
    if release:
        envelope[-release:] *= np.linspace(1, 0, release)
    return envelope


def _tone(event: MusicEvent, samples: int, sample_rate: int, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(samples, dtype=np.float32) / sample_rate
    frequency = 440.0 * (2.0 ** ((event.midi - 69) / 12.0))
    modulation = float(event.cc_controls.get(1, 15)) / 127.0
    vibrato = (0.002 + 0.012 * modulation) * np.sin(2 * np.pi * (4.7 + 1.1 * modulation) * t)
    phase = 2 * np.pi * frequency * t + vibrato
    if event.timbre == "flute":
        signal = 0.90 * np.sin(phase) + 0.08 * np.sin(2 * phase)
        signal += 0.008 * rng.standard_normal(samples)
    elif event.timbre == "oboe":
        signal = sum((0.78 / n) * np.sin(n * phase) for n in range(1, 7))
    elif event.timbre == "clarinet":
        signal = 0.86 * np.sin(phase) + 0.24 * np.sin(3 * phase) + 0.10 * np.sin(5 * phase)
    elif event.timbre == "bassoon":
        signal = 0.78 * np.sin(phase) + 0.28 * np.sin(2 * phase) + 0.18 * np.sin(3 * phase) + 0.08 * np.sin(5 * phase)
    elif event.timbre == "french_horn":
        signal = 0.82 * np.sin(phase) + 0.30 * np.sin(2 * phase) + 0.16 * np.sin(3 * phase) + 0.07 * np.sin(4 * phase)
    elif event.timbre == "violin":
        signal = sum((0.72 / n) * np.sin(n * phase) for n in range(1, 9))
    elif event.timbre == "viola":
        signal = sum((0.70 / (n ** 1.12)) * np.sin(n * phase) for n in range(1, 7))
    elif event.timbre == "cello":
        signal = 0.82 * np.sin(phase) + 0.34 * np.sin(2 * phase) + 0.18 * np.sin(3 * phase) + 0.08 * np.sin(4 * phase)
    elif event.timbre == "orchestral_harp":
        pluck = np.exp(-4.8 * t / max(float(t[-1]) if len(t) else 1.0, 0.05))
        signal = (0.78 * np.sin(phase) + 0.28 * np.sin(2 * phase) + 0.13 * np.sin(3 * phase)) * pluck
    else:
        signal = 0.90 * np.sin(phase) + 0.08 * np.sin(2 * phase)
    peak = float(np.max(np.abs(signal))) if len(signal) else 1.0
    if peak > 1.0:
        signal = signal / peak
    brightness = float(np.clip(event.brightness, 0, 1))
    window = 1 + int(round((1.0 - brightness) * 18.0))
    if window > 1:
        signal = np.convolve(signal, np.ones(window, dtype=np.float32) / window, mode="same")
    return (signal * _adsr(samples, sample_rate)).astype(np.float32)


def _moving_average(signal: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return signal
    kernel = np.ones(window, dtype=np.float32) / window
    return np.column_stack([
        np.convolve(signal[:, channel], kernel, mode="same") for channel in range(signal.shape[1])
    ]).astype(np.float32)


def _modal_nma_frequency(frequency: float, allowed_pitch_classes: set[int] | None) -> tuple[float, int]:
    midi = int(round(69 + 12 * math.log2(max(1e-6, frequency) / 440.0)))
    while midi < 36:
        midi += 12
    while midi > 59:
        midi -= 12
    midi = int(np.clip(midi, 36, 59))
    if allowed_pitch_classes is not None:
        candidates = [m for m in range(36, 60) if m % 12 in allowed_pitch_classes]
        midi = min(candidates, key=lambda m: (abs(m - midi), m))
    return 440.0 * (2.0 ** ((midi - 69) / 12.0)), midi


def render_wav(
    events: list[MusicEvent],
    tempo: int,
    nma: dict | None = None,
    qc_mito_fraction: float = 0.0,
    sample_rate: int = 22050,
    max_seconds: float = 150.0,
    seed: int = 42,
    allowed_pitch_classes: set[int] | None = None,
) -> tuple[bytes, dict]:
    if not events:
        raise ValueError("没有可渲染事件。")
    seconds_per_beat = 60.0 / max(1, tempo)
    requested_seconds = max(e.onset + e.duration for e in events) * seconds_per_beat + 0.25
    total_seconds = min(requested_seconds, max_seconds)
    total_samples = max(1, int(total_seconds * sample_rate))
    stereo = np.zeros((total_samples, 2), dtype=np.float32)
    rng = np.random.default_rng(seed)
    rendered_events = 0
    role_gains = {
        "foreground_melody": 0.34,
        "lossless_codec_carrier": 0.34,
        "derived_counterpoint": 0.27,
        "structural_bass": 0.30,
        "structural_harmony": 0.22,
        "inner_harmony": 0.20,
        "structural_accent": 0.29,
    }
    for event in events:
        start = int(event.onset * seconds_per_beat * sample_rate)
        if start >= total_samples:
            break
        sounding_beats = max(0.03, event.duration * float(event.gate_ratio))
        length = min(int(sounding_beats * seconds_per_beat * sample_rate), total_samples - start)
        if length <= 0:
            continue
        tone = _tone(event, length, sample_rate, rng) * (event.velocity / 127.0) * role_gains.get(event.role, 0.26)
        angle = (event.pan + 1.0) * np.pi / 4.0
        stereo[start:start + length, 0] += tone * np.cos(angle)
        stereo[start:start + length, 1] += tone * np.sin(angle)
        wet = float(np.clip(event.reverb_mix, 0, 0.6))
        width = float(np.clip(event.spatial_width, 0, 1))
        chorus = float(np.clip(event.cc_controls.get(93, 0) / 127.0, 0, 0.65))
        for delay_s, left_gain, right_gain in ((0.012, 0.07, 0.025), (0.019, 0.025, 0.07)):
            delay = int(delay_s * sample_rate)
            chorus_start = start + delay
            chorus_length = min(length, total_samples - chorus_start)
            if chorus_length > 0:
                stereo[chorus_start:chorus_start + chorus_length, 0] += tone[:chorus_length] * chorus * width * left_gain
                stereo[chorus_start:chorus_start + chorus_length, 1] += tone[:chorus_length] * chorus * width * right_gain
        for delay_s, gain in ((0.029 + 0.012 * width, 0.34 * wet), (0.061 + 0.018 * width, 0.22 * wet)):
            delay = int(delay_s * sample_rate)
            wet_start = start + delay
            wet_length = min(length, total_samples - wet_start)
            if wet_length > 0:
                stereo[wet_start:wet_start + wet_length, 0] += tone[:wet_length] * gain * (0.72 + 0.28 * width)
                stereo[wet_start:wet_start + wet_length, 1] += tone[:wet_length] * gain * (0.72 + 0.28 * width)
        rendered_events += 1

    nma_notes: list[int] = []
    if nma and nma.get("available"):
        t = np.arange(total_samples, dtype=np.float32) / sample_rate
        drone = np.zeros(total_samples, dtype=np.float32)
        for mode_index, raw_freq in enumerate(nma.get("audible_frequencies_hz", [])[:3]):
            freq, nma_midi = _modal_nma_frequency(float(raw_freq), allowed_pitch_classes)
            nma_notes.append(nma_midi)
            slow_rate = 0.07 + 0.035 * mode_index
            phase = 2 * np.pi * float(freq) * t + 0.018 * np.sin(2 * np.pi * slow_rate * t + mode_index)
            # A quiet low-string/horn-like spectrum; no electronic/granular timbre.
            breath = 0.78 + 0.22 * np.sin(2 * np.pi * slow_rate * t + 1.3 * mode_index)
            drone += ((np.sin(phase) + 0.24 * np.sin(2 * phase) + 0.10 * np.sin(3 * phase)) * breath).astype(np.float32)
        if np.max(np.abs(drone)) > 0:
            drone /= np.max(np.abs(drone))
            stereo[:, 0] += 0.035 * drone
            stereo[:, 1] += 0.035 * drone

    cutoff_hz = float(700 + (1.0 - np.clip(qc_mito_fraction, 0, 1)) * 9000)
    window = max(1, int(sample_rate / max(700.0, cutoff_hz)))
    stereo = _moving_average(stereo, window)
    # A restrained concert-hall early reflection keeps the procedural preview
    # orchestral without introducing electronic effects or breaking traceability.
    for delay_s, gain in ((0.037, 0.10), (0.071, 0.065)):
        delay = int(delay_s * sample_rate)
        if delay < total_samples:
            stereo[delay:] += gain * stereo[:-delay]
    peak = float(np.max(np.abs(stereo)))
    if peak > 0:
        stereo *= min(0.96 / peak, 1.0)
    pcm = (stereo * 32767).astype("<i2")
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return stream.getvalue(), {
        "duration_seconds": round(total_seconds, 3),
        "requested_duration_seconds": round(requested_seconds, 3),
        "truncated": requested_seconds > max_seconds,
        "rendered_events": rendered_events,
        "sample_rate": sample_rate,
        "channels": 2,
        "qc_lowpass_cutoff_hz": round(cutoff_hz, 1),
        "voice_count": len({event.voice_id for event in events}),
        "voice_event_counts": {
            voice_id: sum(1 for event in events if event.voice_id == voice_id)
            for voice_id in sorted({event.voice_id for event in events})
        },
        "preview_source": "procedural classical-instrument approximation; MIDI/MusicXML carry persistent orchestral programs",
        "nma_sampled_background_notes": nma_notes,
        "nma_scale_quantized": allowed_pitch_classes is not None,
        "strict_scale_pitch_classes": sorted(allowed_pitch_classes) if allowed_pitch_classes is not None else None,
    }

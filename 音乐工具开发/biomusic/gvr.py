from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from .codec import decode_rows, rows_from_events
from .mapping import VOICE_RANGES
from .models import GVRReport, MusicEvent, Violation


def _effective_range(event: MusicEvent, min_midi: int, max_midi: int) -> tuple[int, int]:
    voice_low, voice_high = VOICE_RANGES.get(event.voice_id, (min_midi, max_midi))
    return max(min_midi, voice_low), min(max_midi, voice_high)


def verify_events(
    events: list[MusicEvent],
    min_midi: int,
    max_midi: int,
    tone_rows: list[list[int]] | None = None,
    codec: dict | None = None,
    allowed_pitch_classes: set[int] | None = None,
) -> tuple[list[Violation], dict[str, bool]]:
    violations: list[Violation] = []
    if len({e.event_id for e in events}) != len(events):
        violations.append(Violation(None, "H_event_id", "hard", "事件编号不是全局唯一。"))

    event_ids = {e.event_id for e in events}
    by_voice: dict[str, list[MusicEvent]] = defaultdict(list)
    for event in events:
        by_voice[event.voice_id].append(event)
        low, high = _effective_range(event, min_midi, max_midi)
        if event.duration <= 0:
            violations.append(Violation(event.event_id, "H_duration", "hard", "音符时值必须大于 0。"))
        if not low <= event.midi <= high:
            violations.append(Violation(event.event_id, "H_register", "hard", f"{event.voice_id} 超出 {low}–{high} 的声部音域。"))
        if event.midi % 12 != event.expected_pc:
            violations.append(Violation(event.event_id, "H_mapping", "hard", "实际音级与映射证书不一致。"))
        if allowed_pitch_classes is not None and event.midi % 12 not in allowed_pitch_classes:
            violations.append(Violation(event.event_id, "H_scale", "hard", "实际音级不属于所选主音与调式的允许集合。"))
        if event.source_index < 0 or not event.source_label or not event.mapping_rule:
            violations.append(Violation(event.event_id, "H_trace", "hard", "缺少生物来源或映射理由。"))
        if event.voice_id != "V1_melody" and event.parent_event_id not in event_ids:
            violations.append(Violation(event.event_id, "H_parent", "hard", "派生声部缺少有效的主事件引用。"))

    for voice_id, voice_events in by_voice.items():
        previous_end = 0.0
        for event in sorted(voice_events, key=lambda e: (e.onset, e.event_id)):
            if event.onset < previous_end - 1e-6:
                violations.append(Violation(event.event_id, "H_timeline", "hard", f"{voice_id} 内部发生音符重叠。"))
            previous_end = max(previous_end, event.onset + event.duration)

    # Cross-voice overlap is expected. Exact unisons at the same entrance are reported
    # as a soft orchestration warning rather than treated as a biological error.
    entrance_map: dict[tuple[float, int], list[MusicEvent]] = defaultdict(list)
    for event in events:
        entrance_map[(round(event.onset, 4), event.midi)].append(event)
    for same in entrance_map.values():
        if len({e.voice_id for e in same}) > 1:
            violations.append(Violation(
                same[-1].event_id,
                "S_unison_clarity",
                "soft",
                "多个声部在同一拍进入同一实际音高；保留为允许的管弦乐齐奏。",
            ))

    row_ok = True
    permutation_ok = True
    codec_domain_ok = True
    if tone_rows is not None:
        carrier = sorted(
            (e for e in events if e.voice_id == "V1_melody" and e.is_codec_carrier),
            key=lambda e: (e.codec_block, e.row_position, e.onset, e.event_id),
        )
        try:
            actual_rows = rows_from_events(carrier)
        except ValueError as exc:
            actual_rows = []
            row_ok = False
            permutation_ok = False
            violations.append(Violation(None, "H_permutation", "hard", str(exc)))
        if len(actual_rows) != len(tone_rows):
            row_ok = False
            violations.append(Violation(None, "H_row", "hard", f"载体应有 {len(tone_rows)} 块，实际为 {len(actual_rows)} 块。"))
        for block_index, (actual, expected) in enumerate(zip(actual_rows, tone_rows)):
            if actual != expected:
                row_ok = False
                violations.append(Violation(None, "H_row", "hard", f"第 {block_index + 1} 块音级顺序与编码证书不一致。"))
            if sorted(actual) != list(range(12)):
                permutation_ok = False
                violations.append(Violation(None, "H_permutation", "hard", f"第 {block_index + 1} 块不是完整十二音列。"))
        if codec is not None and actual_rows:
            try:
                decode_rows(actual_rows, codec, verify_checksum=True)
            except ValueError as exc:
                codec_domain_ok = False
                violations.append(Violation(None, "H_codec_domain", "hard", str(exc)))

    hard_rules = {"H_event_id", "H_duration", "H_timeline", "H_register", "H_mapping", "H_trace", "H_parent"}
    if allowed_pitch_classes is not None:
        hard_rules.add("H_scale")
    checks = {rule: not any(v.rule == rule and v.severity == "hard" for v in violations) for rule in sorted(hard_rules)}
    if tone_rows is not None:
        checks["H_row"] = row_ok
        checks["H_permutation"] = permutation_ok
        checks["H_codec_domain"] = codec_domain_ok
    checks["coverage"] = bool(events)
    checks["polyphony"] = len(by_voice) >= 2
    checks["independent_voices"] = all(
        not any(v.rule == "H_timeline" and v.message.startswith(voice_id) for v in violations)
        for voice_id in by_voice
    )
    return violations, checks


def _nearest_pc_in_range(pc: int, target: int, low: int, high: int) -> int:
    candidates = [m for m in range(low, high + 1) if m % 12 == pc]
    return min(candidates, key=lambda m: abs(m - target)) if candidates else max(low, min(high, target))


def repair_events(
    events: list[MusicEvent],
    min_midi: int,
    max_midi: int,
    tone_rows: list[list[int]] | None = None,
    codec: dict | None = None,
    allowed_pitch_classes: set[int] | None = None,
) -> tuple[list[MusicEvent], GVRReport]:
    repaired_events = deepcopy(events)
    before, _ = verify_events(repaired_events, min_midi, max_midi, tone_rows, codec, allowed_pitch_classes)
    repairs: list[Violation] = []
    by_voice: dict[str, list[MusicEvent]] = defaultdict(list)
    for event in repaired_events:
        by_voice[event.voice_id].append(event)

    for voice_id, voice_events in by_voice.items():
        previous_end = 0.0
        for event in sorted(voice_events, key=lambda e: (e.onset, e.event_id)):
            event_repairs = 0
            if event.duration <= 0:
                event.duration = 0.25
                event_repairs += 1
                repairs.append(Violation(event.event_id, "H_duration", "hard", "无效时值", True, "设为十六分音符"))
            if event.onset < previous_end - 1e-6:
                old = event.onset
                event.onset = round(previous_end, 4)
                event_repairs += 1
                repairs.append(Violation(event.event_id, "H_timeline", "hard", f"{voice_id} 内重叠", True, f"起拍由 {old} 后移到 {event.onset}"))
            low, high = _effective_range(event, min_midi, max_midi)
            if allowed_pitch_classes is not None and event.expected_pc not in allowed_pitch_classes:
                old_pc = event.expected_pc
                candidates = [m for m in range(low, high + 1) if m % 12 in allowed_pitch_classes]
                if candidates:
                    event.midi = min(candidates, key=lambda m: (abs(m - event.midi), m))
                    event.expected_pc = event.midi % 12
                    event.mapping_rule += "; GVR 投影到最近的调式内音级"
                    event_repairs += 1
                    repairs.append(Violation(event.event_id, "H_scale", "hard", f"音级 {old_pc} 越出调式", True, f"投影到音级 {event.expected_pc}"))
            target_pc = event.expected_pc
            if event.midi % 12 != target_pc or not low <= event.midi <= high:
                old = event.midi
                event.midi = _nearest_pc_in_range(target_pc, event.midi, low, high)
                event.expected_pc = target_pc
                event_repairs += 1
                repairs.append(Violation(event.event_id, "H_mapping", "hard", f"MIDI {old} 不符合声部约束", True, f"保持音级 {target_pc}，投影到 MIDI {event.midi}"))
            event.status = "repaired" if event_repairs else "retained"
            previous_end = event.onset + event.duration

    repaired_events.sort(key=lambda e: (e.onset, e.voice_id, e.event_id))
    after, checks = verify_events(repaired_events, min_midi, max_midi, tone_rows, codec, allowed_pitch_classes)
    hard_after = [v for v in after if v.severity == "hard"]
    report = GVRReport(
        proposed_count=len(events),
        retained_count=len(repaired_events),
        passed=bool(repaired_events) and not hard_after,
        violations_before=before,
        violations_after=after,
        repairs=repairs,
        checks=checks,
        tone_row=tone_rows[0] if tone_rows else None,
        tone_rows=tone_rows,
        codec=codec,
    )
    return repaired_events, report

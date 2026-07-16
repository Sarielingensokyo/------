from __future__ import annotations

import json
import struct
import unittest
from pathlib import Path
from xml.etree import ElementTree

from biomusic.pipeline import SonificationSettings, run_pipeline
from biomusic.mapping import CLASSICAL_TIMBRES
from biomusic.parsers import parse_uploaded


ROOT = Path(__file__).resolve().parents[1]


class PipelineTests(unittest.TestCase):
    def test_fasta_full_export(self):
        data = (ROOT / "examples" / "example_protein.fasta").read_bytes()
        result = run_pipeline(
            "example_protein.fasta",
            data,
            SonificationSettings(max_events=72, max_audio_seconds=8),
        )
        self.assertTrue(result.report.passed)
        self.assertGreater(len(result.events), 10)
        self.assertTrue(result.wav.startswith(b"RIFF"))
        self.assertTrue(result.midi.startswith(b"MThd"))
        self.assertEqual(ElementTree.fromstring(result.musicxml).tag, "score-partwise")
        root = ElementTree.fromstring(result.musicxml)
        self.assertGreaterEqual(len(root.findall("part")), 5)
        self.assertGreaterEqual(result.summary["voice_count"], 5)
        midi_tracks = struct.unpack(">H", result.midi[10:12])[0]
        self.assertGreaterEqual(midi_tracks, 6)
        self.assertTrue(all(event.timbre in CLASSICAL_TIMBRES for event in result.events))
        by_voice = {}
        for event in result.events:
            by_voice.setdefault(event.voice_id, []).append(event)
        for voice_events in by_voice.values():
            ordered = sorted(voice_events, key=lambda e: (e.onset, e.event_id))
            self.assertTrue(all(a.onset + a.duration <= b.onset + 1e-6 for a, b in zip(ordered, ordered[1:])))
        self.assertTrue(any(
            a.voice_id != b.voice_id and a.onset < b.onset + b.duration and b.onset < a.onset + a.duration
            for i, a in enumerate(result.events) for b in result.events[i + 1:]
        ))
        payload = json.loads(result.report_json.decode("utf-8"))
        self.assertTrue(payload["gvr"]["passed"])

    def test_twelve_tone_cycles_are_verified(self):
        data = (ROOT / "examples" / "example_protein.fasta").read_bytes()
        result = run_pipeline(
            "example_protein.fasta",
            data,
            SonificationSettings(pitch_mode="十二音列 GVR", row_form="RI", max_events=48, max_audio_seconds=6),
        )
        self.assertTrue(result.report.checks["H_row"])
        self.assertEqual(len(result.report.tone_row), 12)
        first_cycle = [e.midi % 12 for e in result.events if e.voice_id == "V1_melody"][:12]
        self.assertEqual(first_cycle, result.report.tone_row)

    def test_pdb_spatial_and_nma(self):
        data = (ROOT / "examples" / "example_structure.pdb").read_bytes()
        result = run_pipeline(
            "example_structure.pdb",
            data,
            SonificationSettings(pitch_mode="生物物理映射", max_audio_seconds=5),
        )
        self.assertTrue(result.nma["available"])
        self.assertIn("spatial_pan", result.record.features)
        self.assertTrue(any(abs(e.pan) > 0.1 for e in result.events))

    def test_csv_qc_filter(self):
        data = (ROOT / "examples" / "example_expression.csv").read_bytes()
        result = run_pipeline(
            "example_expression.csv",
            data,
            SonificationSettings(pitch_mode="生物物理映射", max_audio_seconds=5),
        )
        self.assertEqual(result.record.data_type, "transcriptomics")
        self.assertIn("mitochondrial_fraction", result.record.features)
        self.assertLess(result.audio_info["qc_lowpass_cutoff_hz"], 9700)
        self.assertTrue(all(event.timbre in CLASSICAL_TIMBRES for event in result.events))

    def test_csv_modality_specific_detection(self):
        association = parse_uploaded(
            "gwas.csv",
            b"chr,position,pvalue,effect\n1,101,1e-9,0.3\n1,205,0.04,-0.2\n",
        )[0]
        metabolomics = parse_uploaded(
            "metabolites.csv",
            b"metabolite,abundance,confidence\nA,12.0,0.95\nB,3.0,0.40\n",
        )[0]
        epigenomics = parse_uploaded(
            "methylation.csv",
            b"position,beta_value\n100,0.12\n200,0.88\n",
        )[0]
        self.assertEqual(association.data_type, "association")
        self.assertEqual(metabolomics.data_type, "metabolomics")
        self.assertEqual(epigenomics.data_type, "epigenomics")
        self.assertIn("uncertainty", association.features)
        self.assertIn("uncertainty", metabolomics.features)


if __name__ == "__main__":
    unittest.main()

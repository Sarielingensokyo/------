from __future__ import annotations

import io
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .models import BioRecord


AA3_TO_1 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    "SEC": "U", "PYL": "O",
}


def _clean_sequence(text: str) -> str:
    return re.sub(r"[^A-Za-z]", "", text).upper()


def detect_sequence_type(sequence: str) -> str:
    letters = set(sequence.upper())
    if letters and letters <= set("ACGTN"):
        return "dna"
    if letters and letters <= set("ACGUN"):
        return "rna"
    return "protein"


def parse_fasta(data: bytes | str, forced_type: str = "auto") -> list[BioRecord]:
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    records: list[tuple[str, str]] = []
    name = "sequence_1"
    chunks: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            if chunks:
                records.append((name, _clean_sequence("".join(chunks))))
            name = line[1:].strip() or f"sequence_{len(records) + 1}"
            chunks = []
        else:
            chunks.append(line)
    if chunks:
        records.append((name, _clean_sequence("".join(chunks))))
    if not records and _clean_sequence(text):
        records = [(name, _clean_sequence(text))]
    if not records:
        raise ValueError("FASTA 中没有可识别的序列。")

    output = []
    for rec_name, sequence in records:
        dtype = detect_sequence_type(sequence) if forced_type == "auto" else forced_type
        output.append(BioRecord(
            name=rec_name,
            data_type=dtype,
            symbols=list(sequence),
            source_labels=[str(i + 1) for i in range(len(sequence))],
            metadata={"source_format": "FASTA", "raw_length": len(sequence)},
        ))
    return output


def parse_pdb(data: bytes | str) -> list[BioRecord]:
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    helix_ranges: list[tuple[str, int, int]] = []
    sheet_ranges: list[tuple[str, int, int]] = []
    for line in text.splitlines():
        if line.startswith("HELIX"):
            try:
                helix_ranges.append((line[19].strip() or "_", int(line[21:25]), int(line[33:37])))
            except ValueError:
                pass
        elif line.startswith("SHEET"):
            try:
                sheet_ranges.append((line[21].strip() or "_", int(line[22:26]), int(line[33:37])))
            except ValueError:
                pass

    chains: dict[str, list[tuple[int, str, tuple[float, float, float]]]] = {}
    seen: set[tuple[str, int, str]] = set()
    for line in text.splitlines():
        if not (line.startswith("ATOM") or line.startswith("HETATM")):
            continue
        if line[12:16].strip() != "CA":
            continue
        alt = line[16].strip()
        if alt not in ("", "A"):
            continue
        try:
            chain = line[21].strip() or "_"
            resid = int(line[22:26])
            insertion = line[26].strip()
            key = (chain, resid, insertion)
            if key in seen:
                continue
            seen.add(key)
            residue = AA3_TO_1.get(line[17:20].strip().upper(), "X")
            xyz = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
            chains.setdefault(chain, []).append((resid, residue, xyz))
        except (ValueError, IndexError):
            continue
    if not chains:
        raise ValueError("PDB 中没有找到可用的 CA 原子坐标。")

    output: list[BioRecord] = []
    for chain, residues in chains.items():
        secondary = []
        for resid, _, _ in residues:
            state = "coil"
            if any(c == chain and a <= resid <= b for c, a, b in helix_ranges):
                state = "helix"
            elif any(c == chain and a <= resid <= b for c, a, b in sheet_ranges):
                state = "sheet"
            secondary.append(state)
        output.append(BioRecord(
            name=f"PDB chain {chain}",
            data_type="protein",
            symbols=[r[1] for r in residues],
            source_labels=[f"{chain}:{r[0]}" for r in residues],
            coordinates=[r[2] for r in residues],
            categories={"secondary_structure": secondary},
            metadata={
                "source_format": "PDB",
                "chain": chain,
                "secondary_structure_source": "PDB HELIX/SHEET records; unannotated residues are coil",
            },
        ))
    return output


def _normalise(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    if not finite.any():
        return np.zeros_like(values)
    median = float(np.nanmedian(values[finite]))
    values = np.where(finite, values, median)
    lo, hi = np.percentile(values, [2, 98])
    if hi <= lo:
        return np.zeros_like(values)
    return np.clip((values - lo) / (hi - lo), 0, 1)


def parse_csv(data: bytes | str) -> list[BioRecord]:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    try:
        df = pd.read_csv(io.BytesIO(raw))
    except UnicodeDecodeError:
        df = pd.read_csv(io.BytesIO(raw), encoding="gb18030")
    if df.empty:
        raise ValueError("CSV 没有数据行。")

    lowered = {str(c).lower(): c for c in df.columns}
    sequence_col = next((lowered[k] for k in lowered if k in {"sequence", "seq", "protein_sequence", "dna_sequence"}), None)
    if sequence_col is not None:
        records = []
        for idx, value in df[sequence_col].dropna().items():
            name_col = next((lowered[k] for k in lowered if k in {"name", "id", "sample"}), None)
            name = str(df.loc[idx, name_col]) if name_col is not None else f"row_{idx + 1}"
            records.extend(parse_fasta(f">{name}\n{value}"))
        if records:
            return records

    numeric = df.select_dtypes(include=[np.number]).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if numeric.empty:
        raise ValueError("CSV 需要包含序列列或至少一个数值列。")

    mz_col = next((c for c in numeric.columns if str(c).lower() in {"mz", "m/z", "mass_to_charge"}), None)
    intensity_col = next((c for c in numeric.columns if "intens" in str(c).lower() or "abundance" in str(c).lower()), None)
    if mz_col is not None and intensity_col is not None:
        mz = numeric[mz_col].to_numpy(float)
        intensity = numeric[intensity_col].to_numpy(float)
        return [BioRecord(
            name="mass_spectrometry",
            data_type="mass_spectrometry",
            symbols=["peak"] * len(df),
            source_labels=[str(i + 1) for i in range(len(df))],
            features={"mz": mz.tolist(), "intensity": _normalise(intensity).tolist(), "value": _normalise(mz).tolist()},
            metadata={"source_format": "CSV", "table_shape": list(df.shape)},
        )]

    p_col = next((c for c in numeric.columns if str(c).lower() in {"p", "pvalue", "p_value", "p-value"}), None)
    pos_col = next((c for c in df.columns if str(c).lower() in {"pos", "position", "bp", "coordinate"}), None)
    chr_col = next((c for c in df.columns if str(c).lower() in {"chr", "chrom", "chromosome"}), None)
    effect_col = next((c for c in numeric.columns if str(c).lower() in {"beta", "effect", "effect_size", "odds_ratio", "logfc"}), None)
    if p_col is not None and (pos_col is not None or chr_col is not None):
        pvalues = np.clip(numeric[p_col].to_numpy(float), 1e-300, 1.0)
        salience_raw = -np.log10(pvalues)
        salience = _normalise(salience_raw)
        effect_raw = numeric[effect_col].to_numpy(float) if effect_col is not None else np.zeros(len(df))
        effect = np.tanh(effect_raw).astype(float)
        labels = []
        for i in range(len(df)):
            chrom = str(df.iloc[i][chr_col]) if chr_col is not None else "?"
            pos = str(df.iloc[i][pos_col]) if pos_col is not None else str(i + 1)
            labels.append(f"{chrom}:{pos}")
        return [BioRecord(
            name="association_landscape",
            data_type="association",
            symbols=["variant"] * len(df),
            source_labels=labels,
            features={
                "value": salience.tolist(),
                "significance": salience.tolist(),
                "effect": effect.tolist(),
                "uncertainty": (1.0 - salience).tolist(),
            },
            metadata={
                "source_format": "CSV",
                "table_shape": list(df.shape),
                "mapping_scope": "坐标→时间，-log10(p)→显著性；音乐突出不代表因果。",
            },
        )]

    methyl_col = next((c for c in numeric.columns if any(k in str(c).lower() for k in ("methyl", "cpg", "h3k", "chip", "chromatin", "beta_value"))), None)
    if methyl_col is not None:
        values = _normalise(numeric[methyl_col].to_numpy(float))
        labels = df[pos_col].astype(str).tolist() if pos_col is not None else [str(i + 1) for i in range(len(df))]
        return [BioRecord(
            name="epigenomic_track",
            data_type="epigenomics",
            symbols=[str(methyl_col)] * len(df),
            source_labels=labels,
            features={"value": values.tolist(), "uncertainty": np.zeros(len(df)).tolist()},
            metadata={
                "source_format": "CSV",
                "table_shape": list(df.shape),
                "signal_column": str(methyl_col),
                "mapping_scope": "基因组坐标→时间，调控信号→纹理、力度与和声密度。",
            },
        )]

    metabolite_col = next((c for c in df.columns if str(c).lower() in {"metabolite", "compound", "feature_name", "metabolite_id"}), None)
    confidence_col = next((c for c in numeric.columns if "confiden" in str(c).lower() or "annotation" in str(c).lower()), None)
    if metabolite_col is not None and intensity_col is not None:
        abundance = _normalise(numeric[intensity_col].to_numpy(float))
        confidence = _normalise(numeric[confidence_col].to_numpy(float)) if confidence_col is not None else np.full(len(df), 0.5)
        labels = df[metabolite_col].astype(str).tolist()
        return [BioRecord(
            name="metabolomic_network_table",
            data_type="metabolomics",
            symbols=["metabolite"] * len(df),
            source_labels=labels,
            features={
                "value": abundance.tolist(),
                "abundance": abundance.tolist(),
                "uncertainty": (1.0 - confidence).tolist(),
            },
            metadata={
                "source_format": "CSV",
                "table_shape": list(df.shape),
                "mapping_scope": "丰度表示状态而非通量；注释置信度保留为声学不确定性。",
            },
        )]

    matrix = numeric.to_numpy(float)
    totals = matrix.sum(axis=1)
    detected = (matrix > 0).sum(axis=1)
    mito_cols = [i for i, c in enumerate(numeric.columns) if str(c).upper().startswith(("MT-", "MT_"))]
    mito = matrix[:, mito_cols].sum(axis=1) / np.maximum(totals, 1e-9) if mito_cols else np.zeros(len(df))
    variances = np.var(matrix, axis=0)
    top_n = max(1, min(20, matrix.shape[1]))
    hvg_idx = np.argsort(variances)[-top_n:]
    hvg_score = np.mean(matrix[:, hvg_idx], axis=1)
    row_value = np.mean(matrix, axis=1)
    labels = df.index.astype(str).tolist()
    id_col = next((c for c in df.columns if str(c).lower() in {"cell", "cell_id", "sample", "id"}), None)
    if id_col is not None:
        labels = df[id_col].astype(str).tolist()
    return [BioRecord(
        name="transcriptomic_table",
        data_type="transcriptomics",
        symbols=["cell"] * len(df),
        source_labels=labels,
        features={
            "value": _normalise(row_value).tolist(),
            "total_counts": _normalise(totals).tolist(),
            "detected_features": _normalise(detected).tolist(),
            "mitochondrial_fraction": np.clip(mito, 0, 1).tolist(),
            "hvg_score": _normalise(hvg_score).tolist(),
        },
        metadata={
            "source_format": "CSV",
            "table_shape": list(df.shape),
            "numeric_columns": len(numeric.columns),
            "mitochondrial_columns": len(mito_cols),
            "hvg_columns": [str(numeric.columns[i]) for i in hvg_idx],
            "qc_note": "细胞为行、基因为列的启发式解释；若数据方向相反，请先转置。",
        },
    )]


def parse_uploaded(filename: str, data: bytes, forced_type: str = "auto") -> list[BioRecord]:
    suffix = Path(filename).suffix.lower()
    if suffix in {".fa", ".fasta", ".faa", ".fna"}:
        return parse_fasta(data, forced_type)
    if suffix == ".pdb":
        return parse_pdb(data)
    if suffix == ".csv":
        return parse_csv(data)
    raise ValueError(f"暂不支持 {suffix or '无扩展名'}；请使用 FASTA、PDB 或 CSV。")

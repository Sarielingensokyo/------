from __future__ import annotations

import json
import tarfile
from pathlib import Path

import numpy as np
import pandas as pd

from biomusic.exporters import musicxml_to_pdf
from biomusic.pipeline import SonificationSettings, run_pipeline


ROOT = Path(__file__).resolve().parent
EXPR_DIR = ROOT / "generated_works" / "表达矩阵QC_PBMC3K"
EPI_DIR = ROOT / "generated_works" / "表观甲基化_GSM484237"


def _unique_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    output: list[str] = []
    for raw in names:
        name = raw or "unnamed_gene"
        count = seen.get(name, 0)
        seen[name] = count + 1
        output.append(name if count == 0 else f"{name}__{count + 1}")
    return output


def _matrix_market_stats(path: Path, gene_count: int, cell_count: int, mito_mask: np.ndarray) -> tuple[np.ndarray, ...]:
    gene_sum = np.zeros(gene_count, dtype=float)
    gene_sumsq = np.zeros(gene_count, dtype=float)
    cell_totals = np.zeros(cell_count, dtype=float)
    cell_detected = np.zeros(cell_count, dtype=int)
    mito_counts = np.zeros(cell_count, dtype=float)
    with path.open("r", encoding="ascii") as handle:
        dimensions_seen = False
        for line in handle:
            if line.startswith("%"):
                continue
            if not dimensions_seen:
                rows, cols, _ = map(int, line.split())
                if rows != gene_count or cols != cell_count:
                    raise ValueError(f"Matrix Market shape {rows}x{cols} does not match annotations {gene_count}x{cell_count}.")
                dimensions_seen = True
                continue
            row, col, raw_value = line.split()
            gene_index = int(row) - 1
            cell_index = int(col) - 1
            value = float(raw_value)
            gene_sum[gene_index] += value
            gene_sumsq[gene_index] += value * value
            cell_totals[cell_index] += value
            cell_detected[cell_index] += 1
            if mito_mask[gene_index]:
                mito_counts[cell_index] += value
    return gene_sum, gene_sumsq, cell_totals, cell_detected, mito_counts


def _matrix_market_subset(path: Path, selected_genes: np.ndarray, selected_cells: np.ndarray) -> np.ndarray:
    gene_lookup = {int(source): target for target, source in enumerate(selected_genes)}
    cell_lookup = {int(source): target for target, source in enumerate(selected_cells)}
    dense = np.zeros((len(selected_cells), len(selected_genes)), dtype=int)
    with path.open("r", encoding="ascii") as handle:
        dimensions_seen = False
        for line in handle:
            if line.startswith("%"):
                continue
            if not dimensions_seen:
                dimensions_seen = True
                continue
            row, col, raw_value = line.split()
            gene_target = gene_lookup.get(int(row) - 1)
            cell_target = cell_lookup.get(int(col) - 1)
            if gene_target is not None and cell_target is not None:
                dense[cell_target, gene_target] = int(float(raw_value))
    return dense


def _active_genes_for_cells(path: Path, gene_count: int, selected_cells: np.ndarray) -> np.ndarray:
    selected = set(int(value) for value in selected_cells)
    active = np.zeros(gene_count, dtype=bool)
    with path.open("r", encoding="ascii") as handle:
        dimensions_seen = False
        for line in handle:
            if line.startswith("%"):
                continue
            if not dimensions_seen:
                dimensions_seen = True
                continue
            row, col, _ = line.split()
            if int(col) - 1 in selected:
                active[int(row) - 1] = True
    return np.flatnonzero(active)


def prepare_pbmc3k() -> tuple[Path, dict]:
    archive = EXPR_DIR / "pbmc3k_filtered_gene_bc_matrices.tar.gz"
    extracted = EXPR_DIR / "filtered_gene_bc_matrices"
    if not extracted.exists():
        with tarfile.open(archive, "r:gz") as handle:
            handle.extractall(EXPR_DIR, filter="data")
    matrix_dir = extracted / "hg19"
    genes = pd.read_csv(matrix_dir / "genes.tsv", sep="\t", header=None, names=["gene_id", "gene_symbol"])
    barcodes = pd.read_csv(matrix_dir / "barcodes.tsv", sep="\t", header=None, names=["cell_id"])["cell_id"].astype(str)
    symbols = genes["gene_symbol"].astype(str).to_numpy()
    mito_mask = np.char.startswith(np.char.upper(symbols.astype(str)), "MT-")
    gene_sum, gene_sumsq, totals, detected, mito_counts = _matrix_market_stats(
        matrix_dir / "matrix.mtx", len(genes), len(barcodes), mito_mask
    )
    mito_fraction = mito_counts / np.maximum(totals, 1.0)

    # Keep 240 cells evenly across the QC ordering so both healthy and stressed tails remain audible.
    total_rank = pd.Series(totals).rank(pct=True).to_numpy()
    detected_rank = pd.Series(detected).rank(pct=True).to_numpy()
    qc_order = np.argsort(mito_fraction + 0.18 * total_rank + 0.08 * detected_rank, kind="stable")
    positions = np.linspace(0, len(qc_order) - 1, 240, dtype=int)
    selected_cells = qc_order[positions]

    # Preserve every gene expressed in the selected cells. This is necessary for
    # exact per-cell total counts, detected-gene counts and mitochondrial ratios.
    selected_genes = _active_genes_for_cells(matrix_dir / "matrix.mtx", len(genes), selected_cells)

    dense = _matrix_market_subset(matrix_dir / "matrix.mtx", selected_genes, selected_cells)
    gene_names = _unique_names(symbols[selected_genes].tolist())
    table = pd.DataFrame(dense, columns=gene_names)
    table.insert(0, "cell_id", barcodes.iloc[selected_cells].to_numpy())
    csv_path = EXPR_DIR / "PBMC3K_QC平台输入_240细胞.csv"
    table.to_csv(csv_path, index=False, encoding="utf-8-sig")

    summary = {
        "source": "10x Genomics PBMC3K filtered gene-barcode matrix",
        "source_url": "https://cf.10xgenomics.com/samples/cell/pbmc3k/pbmc3k_filtered_gene_bc_matrices.tar.gz",
        "original_shape_genes_by_cells": [int(len(genes)), int(len(barcodes))],
        "platform_shape_cells_by_columns": [int(table.shape[0]), int(table.shape[1])],
        "selected_cells": int(len(selected_cells)),
        "selected_numeric_genes": int(len(selected_genes)),
        "selected_mitochondrial_genes": [name for name in gene_names if name.upper().startswith("MT-")],
        "full_dataset_qc": {
            "total_counts_median": float(np.median(totals)),
            "detected_genes_median": float(np.median(detected)),
            "mitochondrial_fraction_median": float(np.median(mito_fraction)),
            "mitochondrial_fraction_p95": float(np.quantile(mito_fraction, 0.95)),
        },
        "selection_rule": "240 cells evenly sampled across a deterministic QC ordering; all genes expressed in at least one selected cell are retained so QC denominators remain exact.",
        "platform_interpretation": "rows=cells; numeric columns=genes; MT-* columns drive mitochondrial_fraction; detected genes and top-variance genes drive QC/HVG features.",
    }
    (EXPR_DIR / "PBMC3K_QC处理摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path, summary


def _soft_table(path: Path, begin_marker: str, end_marker: str) -> pd.DataFrame:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = lines.index(begin_marker) + 1
    try:
        end = lines.index(end_marker, start)
    except ValueError:
        end = len(lines)
    rows = [line.split("\t") for line in lines[start:end] if line.strip()]
    return pd.DataFrame(rows[1:], columns=rows[0])


def prepare_epigenomics() -> tuple[Path, dict]:
    sample = _soft_table(EPI_DIR / "GSM484237_full.soft.txt", "!sample_table_begin", "!sample_table_end")
    platform = _soft_table(EPI_DIR / "GPL9183_full.soft.txt", "!platform_table_begin", "!platform_table_end")
    merged = sample.merge(platform, left_on="ID_REF", right_on="ID", how="left", validate="one_to_one")
    output = pd.DataFrame({
        "probe_id": merged["ID_REF"].astype(str),
        "chromosome": merged["Chromosome"].astype(str),
        "position": pd.to_numeric(merged["CpG_Coordinate"], errors="coerce"),
        "gene_symbol": merged["Symbol"].astype(str),
        "cpg_id": merged["cg_no"].astype(str),
        "methylation_beta": pd.to_numeric(merged["VALUE"], errors="coerce"),
        "cpg_island": merged["CpG_island"].astype(str),
        "distance_to_tss": pd.to_numeric(merged["Dist_to_TSS"], errors="coerce"),
    }).dropna(subset=["position", "methylation_beta"])
    output = output[(output["methylation_beta"] >= 0) & (output["methylation_beta"] <= 1)].copy()
    output["position"] = output["position"].astype(int)
    chrom_order = pd.to_numeric(output["chromosome"].str.replace("X", "23").str.replace("Y", "24"), errors="coerce").fillna(99)
    output = output.assign(_chrom_order=chrom_order).sort_values(["_chrom_order", "position", "probe_id"]).drop(columns="_chrom_order")
    csv_path = EPI_DIR / "GSM484237_甲基化平台输入_真实坐标.csv"
    output.to_csv(csv_path, index=False, encoding="utf-8-sig")

    summary = {
        "source": "NCBI GEO GSM484237 (normal human small intestine), platform GPL9183",
        "sample_url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM484237",
        "platform_url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GPL9183",
        "input_sample_rows": int(len(sample)),
        "output_rows_with_coordinates": int(len(output)),
        "chromosomes": sorted(output["chromosome"].unique().tolist()),
        "beta_summary": output["methylation_beta"].describe(percentiles=[0.05, 0.5, 0.95]).to_dict(),
        "processing": "Merged the processed sample beta table to the GPL9183 probe manifest by ID_REF/ID; retained valid beta values and real CpG coordinates; sorted by chromosome and coordinate.",
        "platform_interpretation": "chromosome/position define the genomic timeline; methylation_beta is the epigenomic signal used for pitch degree, dynamics and orchestration density.",
        "scientific_boundary": "This is one normal-tissue sample and demonstrates sonification, not differential methylation or causal inference.",
    }
    (EPI_DIR / "GSM484237_甲基化处理摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path, summary


def render_work(csv_path: Path, out_dir: Path, work_name: str, *, scale_name: str, root_midi: int, tempo: int) -> dict:
    settings = SonificationSettings(
        pitch_mode="生物物理调式映射（推荐）",
        scale_name=scale_name,
        root_midi=root_midi,
        tempo=tempo,
        meter_beats=4,
        meter_beat_type=4,
        max_events=180,
        texture_density=6,
        counterpoint_strength=0.70,
        enable_nma=False,
        max_audio_seconds=120,
        audio_backend="soundfont",
    )
    result = run_pipeline(csv_path.name, csv_path.read_bytes(), settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{work_name}.musicxml").write_bytes(result.musicxml)
    (out_dir / f"{work_name}.mid").write_bytes(result.midi)
    (out_dir / f"{work_name}.wav").write_bytes(result.wav)
    (out_dir / f"{work_name}_音符溯源.csv").write_bytes(result.trace_csv)
    (out_dir / f"{work_name}_GVR报告.json").write_bytes(result.report_json)
    pdf, pdf_message = musicxml_to_pdf(result.musicxml, timeout=120)
    if pdf:
        (out_dir / f"{work_name}.pdf").write_bytes(pdf)
    manifest = {
        "work_name": work_name,
        "input_csv": str(csv_path.name),
        "settings": {
            "pitch_mode": settings.pitch_mode,
            "scale_name": scale_name,
            "root_midi": root_midi,
            "tempo": tempo,
            "meter": "4/4",
            "texture_density": 6,
        },
        "result_summary": result.summary,
        "gvr_checks": result.report.checks,
        "pdf_status": pdf_message,
        "files": sorted(path.name for path in out_dir.iterdir() if path.is_file()),
    }
    (out_dir / f"{work_name}_作品说明.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    expr_csv, expr_summary = prepare_pbmc3k()
    epi_csv, epi_summary = prepare_epigenomics()
    expr_manifest = render_work(
        expr_csv, EXPR_DIR / "乐谱与音频", "PBMC3K_表达矩阵QC_多利亚",
        scale_name="多利亚调式", root_midi=60, tempo=92,
    )
    epi_manifest = render_work(
        epi_csv, EPI_DIR / "乐谱与音频", "GSM484237_小肠甲基化_弗里几亚",
        scale_name="弗里几亚调式", root_midi=62, tempo=76,
    )
    print(json.dumps({
        "expression": {"data": expr_summary, "work": expr_manifest},
        "epigenomics": {"data": epi_summary, "work": epi_manifest},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

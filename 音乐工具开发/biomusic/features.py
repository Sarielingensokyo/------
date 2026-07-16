from __future__ import annotations

import numpy as np

from .models import BioRecord


HYDROPATHY = {
    "I": 4.5, "V": 4.2, "L": 3.8, "F": 2.8, "C": 2.5, "M": 1.9,
    "A": 1.8, "G": -0.4, "T": -0.7, "S": -0.8, "W": -0.9,
    "Y": -1.3, "P": -1.6, "H": -3.2, "E": -3.5, "Q": -3.5,
    "D": -3.5, "N": -3.5, "K": -3.9, "R": -4.5, "X": 0.0,
}
CHARGE = {"D": -1.0, "E": -1.0, "K": 1.0, "R": 1.0, "H": 0.25}
MASS = {
    "A": 89.1, "R": 174.2, "N": 132.1, "D": 133.1, "C": 121.2,
    "Q": 146.2, "E": 147.1, "G": 75.1, "H": 155.2, "I": 131.2,
    "L": 131.2, "K": 146.2, "M": 149.2, "F": 165.2, "P": 115.1,
    "S": 105.1, "T": 119.1, "W": 204.2, "Y": 181.2, "V": 117.1,
    "X": 120.0,
}


def _scale(values: np.ndarray, lo: float | None = None, hi: float | None = None) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    lo = float(np.min(values)) if lo is None else lo
    hi = float(np.max(values)) if hi is None else hi
    if hi <= lo:
        return np.zeros_like(values)
    return np.clip((values - lo) / (hi - lo), 0, 1)


def enrich_record(record: BioRecord) -> BioRecord:
    if record.data_type == "protein":
        hydro = np.array([HYDROPATHY.get(s, 0.0) for s in record.symbols])
        charge = np.array([CHARGE.get(s, 0.0) for s in record.symbols])
        mass = np.array([MASS.get(s, 120.0) for s in record.symbols])
        record.features.update({
            "hydropathy": hydro.tolist(),
            "hydropathy_normalized": _scale(hydro, -4.5, 4.5).tolist(),
            "charge": charge.tolist(),
            "mass_normalized": _scale(mass, 75.0, 205.0).tolist(),
        })
    elif record.data_type in {"dna", "rna"}:
        symbols = np.array(record.symbols)
        gc = np.isin(symbols, ["G", "C"]).astype(float)
        purine = np.isin(symbols, ["A", "G"]).astype(float)
        record.features.update({"gc": gc.tolist(), "purine": purine.tolist(), "value": (0.65 * gc + 0.35 * purine).tolist()})

    if record.coordinates:
        coords = np.asarray(record.coordinates, dtype=float)
        centered = coords - coords.mean(axis=0, keepdims=True)
        spread = np.ptp(centered[:, 0])
        pan = centered[:, 0] / (spread / 2.0) if spread > 1e-9 else np.zeros(len(coords))
        distances = np.linalg.norm(coords[:, None, :] - coords[None, :, :], axis=2)
        contacts = ((distances < 8.0) & (distances > 1e-8)).sum(axis=1)
        record.features.update({
            "spatial_pan": np.clip(pan, -1, 1).tolist(),
            "contact_degree": _scale(contacts.astype(float)).tolist(),
        })
    return record


def compute_coarse_nma(record: BioRecord, cutoff: float = 12.0, max_residues: int = 220) -> dict:
    """Coarse anisotropic-network modes; eigenvalues are relative, not absolute Hz."""
    if not record.coordinates or len(record.coordinates) < 4:
        return {"available": False, "reason": "至少需要 4 个 CA 坐标。"}
    coords = np.asarray(record.coordinates, dtype=float)
    stride = max(1, int(np.ceil(len(coords) / max_residues)))
    sampled = coords[::stride]
    n = len(sampled)
    hessian = np.zeros((3 * n, 3 * n), dtype=float)
    contacts = 0
    for i in range(n):
        for j in range(i + 1, n):
            delta = sampled[j] - sampled[i]
            dist = float(np.linalg.norm(delta))
            if 1e-6 < dist <= cutoff:
                block = -np.outer(delta, delta) / (dist * dist)
                si, sj = slice(3 * i, 3 * i + 3), slice(3 * j, 3 * j + 3)
                hessian[si, sj] += block
                hessian[sj, si] += block
                hessian[si, si] -= block
                hessian[sj, sj] -= block
                contacts += 1
    if contacts == 0:
        return {"available": False, "reason": "给定截断距离内没有残基接触。"}
    eigenvalues = np.linalg.eigvalsh(hessian)
    positive = eigenvalues[eigenvalues > 1e-7]
    if not len(positive):
        return {"available": False, "reason": "未得到稳定的非零模态。"}
    modes = positive[: min(24, len(positive))]
    relative = np.sqrt(modes)
    logv = np.log(relative)
    if len(relative) == 1 or np.ptp(logv) < 1e-9:
        audible = np.array([220.0])
    else:
        audible = np.exp(np.interp(logv, [logv.min(), logv.max()], [np.log(55.0), np.log(1760.0)]))
    return {
        "available": True,
        "model": "CA anisotropic network model (unit springs)",
        "sample_stride": stride,
        "sampled_residues": n,
        "contacts": contacts,
        "relative_eigenvalues": modes.tolist(),
        "audible_frequencies_hz": audible.tolist(),
        "scientific_boundary": "单位弹簧模型的本征值没有绝对频率标定；可听频率仅保持模态间对数比例。",
    }

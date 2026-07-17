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
MAX_ASA = {
    "A": 121.0, "R": 265.0, "N": 187.0, "D": 187.0, "C": 148.0,
    "Q": 214.0, "E": 214.0, "G": 97.0, "H": 216.0, "I": 195.0,
    "L": 191.0, "K": 230.0, "M": 203.0, "F": 228.0, "P": 154.0,
    "S": 143.0, "T": 163.0, "W": 264.0, "Y": 255.0, "V": 165.0,
    "X": 200.0,
}
VDW_RADII = {"C": 1.70, "N": 1.55, "O": 1.52, "S": 1.80, "P": 1.80, "SE": 1.90}


def _scale(values: np.ndarray, lo: float | None = None, hi: float | None = None) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    lo = float(np.min(values)) if lo is None else lo
    hi = float(np.max(values)) if hi is None else hi
    if hi <= lo:
        return np.zeros_like(values)
    return np.clip((values - lo) / (hi - lo), 0, 1)


def _robust_scale(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    if not finite.any():
        return np.zeros_like(values)
    fill = float(np.nanmedian(values[finite]))
    clean = np.where(finite, values, fill)
    lo, hi = np.percentile(clean, [5, 95])
    return _scale(clean, float(lo), float(hi))


def _dihedral(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> float:
    b0 = a - b
    b1 = c - b
    b2 = d - c
    norm = np.linalg.norm(b1)
    if norm < 1e-9:
        return float("nan")
    b1 = b1 / norm
    v = b0 - np.dot(b0, b1) * b1
    w = b2 - np.dot(b2, b1) * b1
    if np.linalg.norm(v) < 1e-9 or np.linalg.norm(w) < 1e-9:
        return float("nan")
    return float(np.degrees(np.arctan2(np.dot(np.cross(b1, v), w), np.dot(v, w))))


def _angle_delta(a: float, b: float) -> float:
    return ((a - b + 180.0) % 360.0) - 180.0


def _backbone_rigidity(record: BioRecord) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = record.length
    phi = np.full(n, np.nan)
    psi = np.full(n, np.nan)
    backbone = record.backbone_atoms or []
    for i in range(n):
        current = backbone[i] if i < len(backbone) else {}
        if i > 0:
            previous = backbone[i - 1] if i - 1 < len(backbone) else {}
            if "C" in previous and all(atom in current for atom in ("N", "CA", "C")):
                phi[i] = _dihedral(
                    np.asarray(previous["C"]), np.asarray(current["N"]),
                    np.asarray(current["CA"]), np.asarray(current["C"]),
                )
        if i + 1 < n:
            following = backbone[i + 1] if i + 1 < len(backbone) else {}
            if all(atom in current for atom in ("N", "CA", "C")) and "N" in following:
                psi[i] = _dihedral(
                    np.asarray(current["N"]), np.asarray(current["CA"]),
                    np.asarray(current["C"]), np.asarray(following["N"]),
                )

    change = np.full(n, np.nan)
    for i in range(1, n):
        if all(np.isfinite([phi[i - 1], psi[i - 1], phi[i], psi[i]])):
            change[i] = np.hypot(_angle_delta(phi[i], phi[i - 1]), _angle_delta(psi[i], psi[i - 1]))
    structure = record.categories.get("secondary_structure", ["coil"] * n)
    baseline = np.array([{"helix": 0.95, "sheet": 0.72, "coil": 0.38}.get(state, 0.38) for state in structure])
    if np.isfinite(change).sum() >= 2:
        flexibility = _robust_scale(change)
        flexibility = np.where(np.isfinite(change), flexibility, 1.0 - baseline)
        rigidity = np.clip(0.55 * baseline + 0.45 * (1.0 - flexibility), 0, 1)
    else:
        rigidity = baseline
        flexibility = 1.0 - baseline
    return phi, psi, rigidity


def _fibonacci_sphere(count: int = 48) -> np.ndarray:
    indices = np.arange(count, dtype=float) + 0.5
    z = 1.0 - 2.0 * indices / count
    radius = np.sqrt(np.maximum(0.0, 1.0 - z * z))
    theta = np.pi * (1.0 + np.sqrt(5.0)) * indices
    return np.column_stack((radius * np.cos(theta), radius * np.sin(theta), z))


def _relative_sasa(record: BioRecord, probe_radius: float = 1.4, max_atoms: int = 6000) -> np.ndarray | None:
    residue_atoms = record.residue_atoms or []
    atom_count = sum(len(atoms) for atoms in residue_atoms)
    # A CA-only trace cannot support atomistic SASA; use an explicit contact proxy instead.
    if atom_count < 2 * max(1, record.length) or atom_count > max_atoms:
        return None
    coords: list[tuple[float, float, float]] = []
    radii: list[float] = []
    owners: list[int] = []
    for residue_index, atoms in enumerate(residue_atoms):
        for element, x, y, z in atoms:
            coords.append((x, y, z))
            radii.append(VDW_RADII.get(element.upper(), 1.70))
            owners.append(residue_index)
    xyz = np.asarray(coords, dtype=float)
    expanded = np.asarray(radii, dtype=float) + probe_radius
    sphere = _fibonacci_sphere(48)
    residue_area = np.zeros(record.length, dtype=float)
    for atom_index, center in enumerate(xyz):
        center_distances = np.linalg.norm(xyz - center, axis=1)
        candidates = np.where((center_distances < expanded[atom_index] + expanded + 0.05) & (np.arange(len(xyz)) != atom_index))[0]
        points = center + sphere * expanded[atom_index]
        if len(candidates):
            delta = points[:, None, :] - xyz[candidates][None, :, :]
            occluded = np.any(np.linalg.norm(delta, axis=2) < expanded[candidates][None, :], axis=1)
            accessible_fraction = 1.0 - float(np.mean(occluded))
        else:
            accessible_fraction = 1.0
        residue_area[owners[atom_index]] += 4.0 * np.pi * expanded[atom_index] ** 2 * accessible_fraction
    maxima = np.array([MAX_ASA.get(symbol, 200.0) for symbol in record.symbols], dtype=float)
    return np.clip(residue_area / maxima, 0, 1)


def enrich_record(record: BioRecord) -> BioRecord:
    if record.data_type == "protein":
        hydro = np.array([HYDROPATHY.get(s, 0.0) for s in record.symbols])
        charge = np.array([CHARGE.get(s, 0.0) for s in record.symbols])
        mass = np.array([MASS.get(s, 120.0) for s in record.symbols])
        sidechain_mass = np.maximum(0.0, mass - 74.06)
        record.features.update({
            "hydropathy": hydro.tolist(),
            "hydropathy_normalized": _scale(hydro, -4.5, 4.5).tolist(),
            "charge": charge.tolist(),
            "mass_normalized": _scale(mass, 75.0, 205.0).tolist(),
            "sidechain_mass_normalized": _scale(sidechain_mass, 0.0, 135.0).tolist(),
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
    if record.data_type == "protein":
        n = record.length
        phi, psi, rigidity = _backbone_rigidity(record)
        relative_sasa = _relative_sasa(record)
        if relative_sasa is None:
            contact = np.asarray(record.features.get("contact_degree", [0.5] * n), dtype=float)
            relative_sasa = np.clip(1.0 - contact, 0, 1)
            record.metadata["sasa_source"] = "CA contact-density exposure proxy; not atomistic SASA"
        else:
            record.metadata["sasa_source"] = "internal Shrake-Rupley approximation, 48 sphere points, 1.4 Å probe"
        b_factors = np.asarray(record.b_factors if record.b_factors else [0.0] * n, dtype=float)
        b_norm = _robust_scale(b_factors)
        hydro_norm = np.asarray(record.features.get("hydropathy_normalized", [0.5] * n), dtype=float)
        wetness = np.clip(relative_sasa * (1.0 - hydro_norm), 0, 1)
        record.features.update({
            "relative_sasa": relative_sasa.tolist(),
            "surface_wetness": wetness.tolist(),
            "b_factor": b_factors.tolist(),
            "b_factor_normalized": b_norm.tolist(),
            "phi_degrees": phi.tolist(),
            "psi_degrees": psi.tolist(),
            "backbone_rigidity": rigidity.tolist(),
            "backbone_flexibility": (1.0 - rigidity).tolist(),
        })
        record.metadata["dihedral_source"] = (
            "N-CA-C backbone circular dihedral changes blended with PDB secondary-structure baseline"
            if record.backbone_atoms and sum(len(atoms) >= 3 for atoms in record.backbone_atoms) >= 3
            else "PDB HELIX/SHEET baseline; insufficient N-CA-C atoms for phi/psi"
        )
        record.metadata["b_factor_source"] = "CA B-factor; predicted-model files may store confidence rather than thermal displacement"
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

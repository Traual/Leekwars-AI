#!/usr/bin/env python3
"""Entraîne une petite tête de transition par perturbations orthogonales.

Huit politiques utilisent les mêmes paires miroir. Les sept colonnes d'un plan de
Hadamard font varier des biais de transition à des échelles comparables ; l'effet de
chaque colonne est ensuite estimé séparément dans chaque paire, ce qui retire la
difficulté propre au combat avant d'agréger. Le candidat exporté rétrécit vers zéro
les directions dont l'effet est incertain.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

from run_benchmark import TRANSITION_BIASES, TRANSITION_SIZE


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "training/run_benchmark.py"
DEFAULT_REPORTS = ROOT / "training/results/transition-orthogonal"

# Les échelles mettent chaque direction dans une plage de quelques unités à quelques
# dizaines d'unités de score sur une action typique. DELTA/EFFICIENCY sont exclus : ils
# recopieraient directement la valeur terminale que la tête doit compléter. LIFE et
# MAX_LIFE sont déjà fortement corrélés à CAPABILITY/ALIVE dans ce petit plan.
FEATURE_SCALES: tuple[tuple[str, float], ...] = (
    ("CAPABILITY", 0.25),
    ("PERIODIC", 0.05),
    ("SHIELD", 1.0),
    ("ALIVE", 0.025),
    ("TP_LEFT", 10.0),
    ("COST", 1.0),
    ("COOLDOWN", 1.0),
)


def hadamard(order: int) -> list[list[int]]:
    """Matrice de Sylvester d'ordre puissance de deux."""
    if order < 1 or order & (order - 1):
        raise ValueError("l'ordre de Hadamard doit être une puissance de deux")
    matrix = [[1]]
    while len(matrix) < order:
        matrix = (
            [row + row for row in matrix]
            + [row + [-value for value in row] for row in matrix]
        )
    return matrix


def design_rows() -> list[list[int]]:
    # La première colonne, constante, est l'intercept. Les sept autres sont les facteurs.
    return [row[1:] for row in hadamard(8)]


def vector_for(row: list[int]) -> list[float]:
    if len(row) != len(FEATURE_SCALES):
        raise ValueError("ligne de plan incompatible avec les sept facteurs")
    vector = [0.0] * TRANSITION_SIZE
    for code, (name, scale) in zip(row, FEATURE_SCALES):
        vector[TRANSITION_BIASES[name]] = code * scale
    return vector


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def design_paths(reports_dir: Path) -> list[tuple[Path, Path]]:
    paths = []
    for index, row in enumerate(design_rows()):
        model = reports_dir / "models" / f"design-{index:02d}.json"
        report = reports_dir / f"design-{index:02d}.json"
        write_json(
            model,
            {
                "transition_vector": vector_for(row),
                "design_index": index,
                "design_codes": {
                    name: code for code, (name, _scale) in zip(row, FEATURE_SCALES)
                },
                "feature_scales": dict(FEATURE_SCALES),
            },
        )
        paths.append((model, report))
    return paths


def report_is_current(
    report_path: Path,
    model_path: Path,
    pairs: int,
    max_turns: int,
    selection_seed: int,
) -> bool:
    if not report_path.exists():
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        report.get("mode") == "farmer"
        and report.get("pairs") == pairs
        and report.get("max_turns") == max_turns
        and report.get("selection_seed") == selection_seed
        and report.get("candidate_transition_sha256") == sha256(model_path)
    )


def run_one(
    index: int,
    model_path: Path,
    report_path: Path,
    args: argparse.Namespace,
) -> Path:
    if report_is_current(
        report_path, model_path, args.pairs, args.max_turns, args.selection_seed
    ):
        print(f"[{index + 1}/8] reprise du rapport", flush=True)
        return report_path
    command = [
        sys.executable,
        str(BENCHMARK),
        "--mode", "farmer",
        "--pairs", str(args.pairs),
        "--max-turns", str(args.max_turns),
        "--selection-seed", str(args.selection_seed),
        "--jobs", "1",
        "--batch-size", str(args.batch_size),
        "--transition-vector", str(model_path),
        "--name", f"transition-orthogonal-{index:02d}",
        "--generator", str(args.generator),
        "--output", str(report_path),
    ]
    if args.java_home:
        command.extend(("--java-home", str(args.java_home)))
    log_path = report_path.with_suffix(".log")
    print(f"[{index + 1}/8] lancement", flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        tail = "\n".join(log_path.read_text(encoding="utf-8").splitlines()[-30:])
        raise RuntimeError(f"variante {index} en échec:\n{tail}")
    print(f"[{index + 1}/8] terminé", flush=True)
    return report_path


def load_reports(report_paths: list[Path]) -> list[dict[str, Any]]:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in report_paths]
    expected = set(range(len(reports[0]["details"])))
    for report in reports:
        indices = {int(detail["index"]) for detail in report["details"]}
        if indices != expected:
            raise ValueError("les variantes n'ont pas exactement les mêmes indices de paires")
    return reports


def estimate_effects(reports: list[dict[str, Any]]) -> list[dict[str, float | str]]:
    rows = design_rows()
    if len(reports) != len(rows):
        raise ValueError("huit rapports sont requis pour le plan orthogonal")
    by_report = [
        {int(detail["index"]): float(detail["fitness"]) for detail in report["details"]}
        for report in reports
    ]
    pair_indices = sorted(by_report[0])
    if any(sorted(values) != pair_indices for values in by_report[1:]):
        raise ValueError("les variantes n'utilisent pas les mêmes paires")

    estimates: list[dict[str, float | str]] = []
    for factor, (name, scale) in enumerate(FEATURE_SCALES):
        # Orthogonalité : somme(code_i * réponse_i) / 8, séparément dans chaque
        # paire. Son intercept/difficulté disparaît puisque chaque colonne somme à zéro.
        per_pair = [
            sum(rows[variant][factor] * by_report[variant][pair] for variant in range(8))
            / 8
            for pair in pair_indices
        ]
        mean = statistics.fmean(per_pair)
        standard_error = (
            statistics.stdev(per_pair) / math.sqrt(len(per_pair))
            if len(per_pair) > 1 else 0.0
        )
        estimates.append(
            {
                "feature": name,
                "scale": scale,
                "effect": mean,
                "standard_error": standard_error,
                "t_ratio": mean / standard_error if standard_error else math.inf,
            }
        )
    return estimates


def trained_vector(estimates: list[dict[str, float | str]]) -> tuple[list[float], dict[str, float]]:
    vector = [0.0] * TRANSITION_SIZE
    biases: dict[str, float] = {}
    for estimate in estimates:
        name = str(estimate["feature"])
        scale = float(estimate["scale"])
        effect = float(estimate["effect"])
        standard_error = float(estimate["standard_error"])
        # Rétrécissement empirique borné : |t|=1 conserve la moitié de l'échelle,
        # |t|=2 en conserve 80 %, une direction sans signal revient exactement à zéro.
        if effect == 0:
            weight = 0.0
        elif standard_error == 0:
            weight = math.copysign(scale, effect)
        else:
            t2 = (effect / standard_error) ** 2
            weight = math.copysign(scale * t2 / (1 + t2), effect)
        vector[TRANSITION_BIASES[name]] = weight
        biases[name] = weight
    return vector, biases


def fit(report_paths: list[Path], output: Path) -> Path:
    reports = load_reports(report_paths)
    estimates = estimate_effects(reports)
    vector, biases = trained_vector(estimates)
    write_json(
        output,
        {
            "transition_vector": vector,
            "transition_biases": biases,
            "effects": estimates,
            "training": {
                "method": "hadamard-8 paired fixed-effects with t-ratio shrinkage",
                "feature_scales": dict(FEATURE_SCALES),
                "reports": [str(path) for path in report_paths],
                "report_sha256": {str(path): sha256(path) for path in report_paths},
            },
        },
    )
    print(json.dumps({"candidate": str(output), "biases": biases, "effects": estimates}, indent=2))
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", type=int, default=16)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--selection-seed", type=int, default=2026082001)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--generator", type=Path, default=ROOT.parent / "leek-wars-generator")
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS)
    parser.add_argument("--fit-only", action="store_true")
    args = parser.parse_args()
    args.generator = args.generator.resolve()
    args.reports_dir = args.reports_dir.resolve()

    paths = design_paths(args.reports_dir)
    if not args.fit_only:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [
                pool.submit(run_one, index, model, report, args)
                for index, (model, report) in enumerate(paths)
            ]
            for future in concurrent.futures.as_completed(futures):
                future.result()
    fit([report for _model, report in paths], args.reports_dir / "trained-transition.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

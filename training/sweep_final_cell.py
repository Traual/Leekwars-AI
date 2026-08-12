#!/usr/bin/env python3
"""Criblage sequentiel et reprenable des sorties dormantes de FinalCell."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "training/run_benchmark.py"
DEFAULT_RESULTS = ROOT / "training/results/final-cell-sweep"
DEFAULT_CANDIDATES = (
    "net-factor-050:NET=-0.5",
    "net-factor-075:NET=-0.25",
    "net-factor-0875:NET=-0.125",
    "net-factor-150:NET=0.5",
    "net-factor-200:NET=1",
    "target-2:TARGET_DISTANCE=2",
    "target-4:TARGET_DISTANCE=4",
    "target-6:TARGET_DISTANCE=6",
    "heal-003:HEAL_COST=0.03",
    "crowding-050:CROWDING=0.5",
    "distance-050:DISTANCE=0.5",
)


def parse_candidate(raw: str) -> tuple[str, str, str | None]:
    name, separator, specification = raw.partition(":")
    if not separator or not name or not specification:
        raise ValueError(
            f"candidat invalide {raw!r}; format NOM:BIAIS=VALEUR[,..][;INDEX=VALEUR,..]"
        )
    biases, index_separator, indices = specification.partition(";")
    return name, biases, indices if index_separator else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", action="append", default=[])
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--mode", choices=("farmer", "solo", "br"), default="farmer")
    parser.add_argument("--pairs", type=int, default=2)
    parser.add_argument("--max-turns", type=int, default=2)
    parser.add_argument("--selection-seed", type=int, default=20260812)
    parser.add_argument("--min-cores", type=int, default=1024)
    parser.add_argument("--jobs", type=int, choices=(1, 2), default=1)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    candidates = tuple(args.candidate) if args.candidate else DEFAULT_CANDIDATES
    args.results.mkdir(parents=True, exist_ok=True)
    reports = []
    for position, raw in enumerate(candidates, 1):
        name, biases, indices = parse_candidate(raw)
        output = args.results / f"{name}.json"
        if output.exists() and not args.force:
            print(f"[{position}/{len(candidates)}] {name}: reprise du rapport", flush=True)
        else:
            print(f"[{position}/{len(candidates)}] {name}: lancement", flush=True)
            command = [
                sys.executable, str(BENCHMARK),
                "--mode", args.mode,
                "--pairs", str(args.pairs),
                "--max-turns", str(args.max_turns),
                "--selection-seed", str(args.selection_seed),
                "--min-cores", str(args.min_cores),
                "--jobs", str(args.jobs),
                "--batch-size", str(args.batch_size),
                "--timeout", str(args.timeout),
                "--name", name,
                "--bias", biases,
                "--output", str(output),
            ]
            if indices:
                command.extend(("--index", indices))
            if args.java_home:
                command.extend(("--java-home", str(args.java_home)))
            completed = subprocess.run(command, cwd=ROOT, text=True)
            if completed.returncode != 0:
                raise RuntimeError(f"benchmark {name} termine avec {completed.returncode}")
        reports.append(json.loads(output.read_text(encoding="utf-8")))

    ranking = sorted(reports, key=lambda report: report["mean_fitness"], reverse=True)
    print("\nClassement du criblage:")
    for report in ranking:
        print(
            f"{report['name']:18s} fitness={report['mean_fitness']:+.4f} "
            f"SE={report['standard_error']:.4f} "
            f"ops(total, diagnostic)={report['total_operations_diagnostic']['ratio']:.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

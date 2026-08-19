#!/usr/bin/env python3
"""Benchmarks miroires du scoring contre le checkpoint courant.

Les deux combats d'une paire ont exactement builds, carte et graine identiques ;
seule l'affectation candidat/reference aux deux camps est inversee.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import random
import re
import shutil
import statistics
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GENERATOR = ROOT.parent / "leek-wars-generator"
DEFAULT_DATA = ROOT / "training/data/meta_builds.jsonl"
RUNNER_SOURCE = ROOT / "training/tools/BatchRunner.java"
RUNNER_BUILD = ROOT / "training/.build"
RESULT_PREFIX = "__TRAUAL_RESULT__\t"

# Layout documente dans ContextualWeights.leek.
MODEL_BIASES = {
    "DISTANCE": 899 + 6,
    "COVID": 899 + 7,
    "TARGET_DISTANCE": 899 + 8,
    "NET": 972,
    "HEAL_COST": 973,
    "CENTER": 974,
    "CROWDING": 975,
}

TRANSITION_FEATURES = (
    "DELTA", "EFFICIENCY", "CAPABILITY", "PERIODIC",
    "LIFE", "MAX_LIFE", "SHIELD", "ALIVE",
    "TP_LEFT", "MP_LEFT", "COOLDOWN", "COST",
)
TRANSITION_INPUTS = 8
TRANSITION_OUTPUTS = len(TRANSITION_FEATURES)
TRANSITION_SIZE = TRANSITION_INPUTS * TRANSITION_OUTPUTS + TRANSITION_OUTPUTS
TRANSITION_BIASES = {
    name: TRANSITION_INPUTS * TRANSITION_OUTPUTS + index
    for index, name in enumerate(TRANSITION_FEATURES)
}


@dataclass(frozen=True)
class PairSpec:
    mode: str
    seed: int
    left_ids: tuple[int, ...]
    right_ids: tuple[int, ...]


def load_records(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[int, dict[str, Any]]]:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    solo = sorted((r for r in records if r["kind"] == "solo_ranking"), key=lambda r: r["rank"])
    farmers = sorted((r for r in records if r["kind"] == "farmer_ranking"), key=lambda r: r["rank"])
    builds = {int(r["id"]): r for r in records if r["kind"] == "build"}
    return solo, farmers, builds


def model_vector(path: Path) -> tuple[list[float], re.Match[str], str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"global NN_MODEL = \[(.*?)\]", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"NN_MODEL introuvable dans {path}")
    vector = json.loads("[" + match.group(1) + "]")
    if len(vector) != 976:
        raise ValueError(f"vecteur NN inattendu: {len(vector)} au lieu de 976")
    return vector, match, text


def vector_file(path: Path) -> list[float]:
    if path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        vector = payload.get("vector") if isinstance(payload, dict) else payload
    else:
        vector, _, _ = model_vector(path)
    if not isinstance(vector, list) or len(vector) != 976:
        raise ValueError(f"vecteur candidat inattendu dans {path}: longueur 976 requise")
    return [float(value) for value in vector]


def transition_vector_file(path: Path) -> list[float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    vector = payload.get("transition_vector") if isinstance(payload, dict) else payload
    if not isinstance(vector, list) or len(vector) != TRANSITION_SIZE:
        raise ValueError(
            f"tête de transition inattendue dans {path}: longueur {TRANSITION_SIZE} requise"
        )
    return [float(value) for value in vector]


def patch_transition_model(
    ai_dir: Path,
    biases: dict[str, float],
    replacement: list[float] | None = None,
) -> None:
    if not biases and replacement is None:
        return
    path = ai_dir / "Scoring/TransitionModel.leek"
    text = path.read_text(encoding="utf-8")
    match = re.search(r"global NN_TRANSITION_MODEL = (null|\[(.*?)\])", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"NN_TRANSITION_MODEL introuvable dans {path}")
    vector = [0.0] * TRANSITION_SIZE if replacement is None else replacement.copy()
    if len(vector) != TRANSITION_SIZE:
        raise ValueError(f"tête de transition: {len(vector)} paramètres, attendu {TRANSITION_SIZE}")
    for name, value in biases.items():
        vector[TRANSITION_BIASES[name]] = value
    rendered = "global NN_TRANSITION_MODEL = " + json.dumps(vector, separators=(",", ":"))
    path.write_text(text[: match.start()] + rendered + text[match.end() :], encoding="utf-8")


def patch_model(
    ai_dir: Path,
    biases: dict[str, float],
    indices: dict[int, float],
    replacement: list[float] | None = None,
) -> None:
    path = ai_dir / "Scoring/NNModel.leek"
    vector, match, text = model_vector(path)
    if replacement is not None:
        vector = replacement.copy()
    for name, value in biases.items():
        vector[MODEL_BIASES[name]] = value
    for index, value in indices.items():
        vector[index] = value
    rendered = "global NN_MODEL = " + json.dumps(vector, separators=(",", ":"))
    path.write_text(text[: match.start()] + rendered + text[match.end() :], encoding="utf-8")


def enable_profiler(ai_dir: Path) -> None:
    path = ai_dir / "Utils/Benchmark.leek"
    text = path.read_text(encoding="utf-8")
    replaced, count = re.subn(r"static ENABLED = false", "static ENABLED = true", text, count=1)
    if count != 1:
        raise ValueError(f"commutateur BenchOps introuvable dans {path}")
    marker = "\t\t\tvar avg = floor(ops / calls)"
    if marker not in replaced:
        raise ValueError(f"point d'instrumentation BenchOps introuvable dans {path}")
    replaced = replaced.replace(
        marker,
        marker + '\n\t\t\tdebug("__PROFILE__|" + n + "|" + ops + "|" + calls + "|" + fullTurn)',
        1,
    )
    path.write_text(replaced, encoding="utf-8")


def parse_assignments(values: list[str], allowed_names: bool) -> dict[Any, float]:
    result: dict[Any, float] = {}
    for raw in values:
        for assignment in raw.split(","):
            if not assignment:
                continue
            key, separator, value = assignment.partition("=")
            if not separator:
                raise ValueError(f"affectation invalide: {assignment}")
            if allowed_names:
                key = key.upper()
                if key not in MODEL_BIASES:
                    raise ValueError(f"biais inconnu {key}; choix: {', '.join(MODEL_BIASES)}")
            else:
                key = int(key)
                if not 0 <= key < 976:
                    raise ValueError(f"index NN hors limites: {key}")
            result[key] = float(value)
    return result


def parse_transition_assignments(values: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for raw in values:
        for assignment in raw.split(","):
            if not assignment:
                continue
            key, separator, value = assignment.partition("=")
            key = key.upper()
            if not separator or key not in TRANSITION_BIASES:
                raise ValueError(
                    f"transition invalide {assignment}; choix: {', '.join(TRANSITION_FEATURES)}"
                )
            result[key] = float(value)
    return result


def make_pairs(
    mode: str,
    count: int,
    selection_seed: int,
    solo: list[dict[str, Any]],
    farmers: list[dict[str, Any]],
    builds: dict[int, dict[str, Any]],
) -> list[PairSpec]:
    rng = random.Random(selection_seed)
    pairs = []
    if mode == "solo":
        pool = [int(row["id"]) for row in solo if int(row["id"]) in builds]
        if len(pool) < 2:
            raise ValueError("pas assez de builds solo")
        for _ in range(count):
            left, right = rng.sample(pool, 2)
            pairs.append(PairSpec(mode, rng.randrange(1, 2**31), (left,), (right,)))
    elif mode == "farmer":
        pool = []
        for row in farmers:
            ids = tuple(int(value) for value in row["leek_ids"] if int(value) in builds)
            if len(ids) == 4:
                pool.append(ids)
        if len(pool) < 2:
            raise ValueError("pas assez d'equipes eleveur completes")
        for _ in range(count):
            left, right = rng.sample(pool, 2)
            pairs.append(PairSpec(mode, rng.randrange(1, 2**31), left, right))
    elif mode == "br":
        pool = [int(row["id"]) for row in solo if int(row["id"]) in builds]
        if len(pool) < 10:
            raise ValueError("pas assez de builds solo pour une BR a 10")
        for _ in range(count):
            ids = tuple(rng.sample(pool, 10))
            pairs.append(PairSpec(mode, rng.randrange(1, 2**31), ids, ()))
    else:
        raise ValueError(f"mode non pris en charge: {mode}")
    return pairs


def entity(
    build: dict[str, Any],
    entity_id: int,
    team: int,
    farmer: int,
    ai: Path,
    min_cores: int,
) -> dict[str, Any]:
    stats = build["stats"]
    return {
        "id": entity_id,
        "ai": str(ai),
        "ai_owner": farmer,
        "name": build["name"],
        "type": 0,
        "farmer": farmer,
        "team": team,
        "level": build["level"],
        "life": stats["life"],
        "strength": stats["strength"],
        "wisdom": stats["wisdom"],
        "agility": stats["agility"],
        "resistance": stats["resistance"],
        "science": stats["science"],
        "magic": stats["magic"],
        "frequency": stats["frequency"],
        # L'IA ne lit pas le budget. Une grande valeur ne change donc pas son arbre ; elle
        # evite seulement qu'un combat d'entrainement soit coupe par le plafond moteur.
        "cores": max(min_cores, stats["cores"]),
        "ram": stats["ram"],
        "tp": stats["tp"],
        "mp": stats["mp"],
        "weapons": [item["template"] for item in build["weapons"]],
        "chips": [item["template"] for item in build["chips"]],
    }


def write_scenario(
    path: Path,
    pair: PairSpec,
    builds: dict[int, dict[str, Any]],
    candidate: Path,
    reference: Path,
    candidate_team: int,
    max_turns: int,
    min_cores: int,
) -> None:
    if pair.mode == "br":
        groups = []
        farmers = []
        teams = []
        for position, build_id in enumerate(pair.left_ids, 1):
            is_candidate = position <= 5 if candidate_team == 1 else position > 5
            groups.append([
                entity(
                    builds[build_id],
                    entity_id=position * 10000 + 1,
                    team=position,
                    farmer=position,
                    ai=candidate if is_candidate else reference,
                    min_cores=min_cores,
                )
            ])
            farmers.append({"id": position, "name": f"br-{position}", "country": "fr"})
            teams.append({"id": position, "name": f"br-{position}"})
        scenario = {
            "farmers": farmers,
            "teams": teams,
            "entities": groups,
            "fight_type": 3,
            "fight_context": 5,
            "random_seed": pair.seed,
            "max_turns": max_turns,
        }
        path.write_text(
            json.dumps(scenario, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        return

    ai_by_team = {
        1: candidate if candidate_team == 1 else reference,
        2: candidate if candidate_team == 2 else reference,
    }
    groups = []
    for team, ids in ((1, pair.left_ids), (2, pair.right_ids)):
        groups.append([
            entity(
                builds[build_id],
                entity_id=team * 10000 + position,
                team=team,
                farmer=team,
                ai=ai_by_team[team],
                min_cores=min_cores,
            )
            for position, build_id in enumerate(ids, 1)
        ])
    scenario = {
        "farmers": [
            {"id": 1, "name": "candidate-side-1", "country": "fr"},
            {"id": 2, "name": "candidate-side-2", "country": "fr"},
        ],
        "teams": [{"id": 1, "name": "left"}, {"id": 2, "name": "right"}],
        "entities": groups,
        "fight_type": 1 if pair.mode == "farmer" else 0,
        "fight_context": 2,
        "random_seed": pair.seed,
        "max_turns": max_turns,
    }
    path.write_text(json.dumps(scenario, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def compile_runner(generator: Path, java_home: Path) -> None:
    RUNNER_BUILD.mkdir(parents=True, exist_ok=True)
    class_file = RUNNER_BUILD / "training/tools/BatchRunner.class"
    newest_input = max(RUNNER_SOURCE.stat().st_mtime, (generator / "generator.jar").stat().st_mtime)
    if class_file.exists() and class_file.stat().st_mtime >= newest_input:
        return
    subprocess.run(
        [
            str(java_home / "bin/javac"),
            "-cp", str(generator / "generator.jar"),
            "-d", str(RUNNER_BUILD),
            str(RUNNER_SOURCE),
        ],
        check=True,
    )


def resolve_java_home(generator: Path, requested: Path | None) -> Path:
    if requested is not None:
        home = requested
    else:
        bundled = generator.parent / "tools/jdk"
        java = shutil.which("java")
        home = bundled if (bundled / "bin/java").exists() else (
            Path(java).resolve().parent.parent if java else bundled
        )
    if not (home / "bin/java").exists() or not (home / "bin/javac").exists():
        raise FileNotFoundError(
            f"JDK introuvable dans {home}; utiliser --java-home avec un JDK complet"
        )
    return home


def run_batch(
    scenarios: list[Path], generator: Path, java_home: Path, timeout: int, profile_ops: bool
) -> list[dict[str, Any]]:
    classpath = f"{generator / 'generator.jar'}:{RUNNER_BUILD}"
    command = [
        str(java_home / "bin/java"), "-Xmx3g",
        *( ["-Dtraual.profile=true"] if profile_ops else [] ), "-cp", classpath,
        "training.tools.BatchRunner", *(str(path) for path in scenarios),
    ]
    completed = subprocess.run(
        command,
        cwd=generator,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"BatchRunner code {completed.returncode}:\n{completed.stdout[-8000:]}")
    results: dict[int, dict[str, Any]] = {}
    for line in completed.stdout.splitlines():
        if line.startswith(RESULT_PREFIX):
            _, index, payload = line.split("\t", 2)
            results[int(index)] = json.loads(payload)
    if len(results) != len(scenarios):
        raise RuntimeError(
            f"BatchRunner: {len(results)}/{len(scenarios)} resultats; fin de sortie:\n"
            + completed.stdout[-8000:]
        )
    return [results[index] for index in range(len(scenarios))]


def chunks(values: list[Path], size: int) -> list[list[Path]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def survival(result: dict[str, Any], team: int) -> float:
    entities = [e for e in result["entities"] if e["team"] == team and not e["summon"]]
    initial = sum(e["initial_life"] for e in entities)
    # Signal borne dans [0, 1]. La vitalite temporaire est utile au combat mais ne doit pas
    # faire sortir la fitness de son echelle et artificiellement reduire/augmenter sa variance.
    remaining = sum(min(e["final_life"], e["initial_life"]) for e in entities)
    return remaining / initial if initial else 0.0


def operations(result: dict[str, Any], team: int) -> int:
    return sum(e["operations"] for e in result["entities"] if e["team"] == team and not e["summon"])


def cohort_survival(result: dict[str, Any], teams: set[int]) -> float:
    entities = [e for e in result["entities"] if e["team"] in teams and not e["summon"]]
    initial = sum(e["initial_life"] for e in entities)
    remaining = sum(min(e["final_life"], e["initial_life"]) for e in entities)
    return remaining / initial if initial else 0.0


def cohort_operations(result: dict[str, Any], teams: set[int]) -> int:
    return sum(e["operations"] for e in result["entities"] if e["team"] in teams and not e["summon"])


PROFILE_LINE = re.compile(
    r"^\s*(?P<section>[A-Za-z]+)\s*:\s*\d+k ops\s*\|\s*"
    r"(?P<calls>\d+) calls\s*\|\s*avg (?P<average>\d+)k/call"
)
PROFILE_EXACT = re.compile(
    r"^__PROFILE__\|(?P<section>[A-Za-z]+)\|(?P<ops>\d+)\|(?P<calls>\d+)\|(?P<turn>\d+)$"
)


def profile_lines(result: dict[str, Any], teams: set[int]) -> dict[str, list[tuple[int, int]]]:
    ids = {
        e["id"] for e in result["entities"]
        if e["team"] in teams and not e["summon"]
    }
    exact_sections: dict[str, list[tuple[int, int]]] = {}
    approximate_sections: dict[str, list[tuple[int, int]]] = {}
    for farmer_log in (result.get("logs") or {}).values():
        for entries in farmer_log.values():
            full_turn_seen: set[int] = set()
            for entry in entries:
                if len(entry) < 3 or entry[0] not in ids or not isinstance(entry[2], str):
                    continue
                exact = PROFILE_EXACT.match(entry[2])
                if exact:
                    calls = int(exact.group("calls"))
                    exact_sections.setdefault(exact.group("section"), []).append(
                        (calls, int(exact.group("ops")))
                    )
                    if entry[0] not in full_turn_seen:
                        exact_sections.setdefault("FullTurn", []).append(
                            (1, int(exact.group("turn")))
                        )
                        full_turn_seen.add(entry[0])
                    continue
                match = PROFILE_LINE.match(entry[2])
                if match:
                    approximate_sections.setdefault(match.group("section"), []).append(
                        (
                            int(match.group("calls")),
                            int(match.group("calls")) * int(match.group("average")) * 1000,
                        )
                    )
    return {
        section: exact_sections.get(section, approximate_sections.get(section, []))
        for section in set(exact_sections) | set(approximate_sections)
    }


def summarize_profile(results: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    buckets: dict[str, dict[str, list[tuple[int, int]]]] = {
        "candidate": {}, "reference": {}
    }
    for index, result in enumerate(results):
        if mode == "br":
            candidate_teams = set(range(1, 6)) if index % 2 == 0 else set(range(6, 11))
            all_teams = set(range(1, 11))
            roles = (("candidate", candidate_teams), ("reference", all_teams - candidate_teams))
        else:
            candidate_team = 1 if index % 2 == 0 else 2
            roles = (("candidate", {candidate_team}), ("reference", {3 - candidate_team}))
        for role, teams in roles:
            for section, values in profile_lines(result, teams).items():
                buckets[role].setdefault(section, []).extend(values)
    report: dict[str, Any] = {}
    for section in sorted(set(buckets["candidate"]) | set(buckets["reference"])):
        row = {}
        for role in ("candidate", "reference"):
            values = buckets[role].get(section, [])
            calls = sum(value[0] for value in values)
            total = sum(value[1] for value in values)
            row[role + "_average_ops_per_call"] = total / calls if calls else None
            row[role + "_calls"] = calls
        candidate = row["candidate_average_ops_per_call"]
        reference = row["reference_average_ops_per_call"]
        row["ratio"] = candidate / reference if candidate is not None and reference else None
        report[section] = row
    return report


def summarize(pairs: list[PairSpec], results: list[dict[str, Any]], profile_ops: bool) -> dict[str, Any]:
    if len(results) != 2 * len(pairs):
        raise ValueError("nombre de resultats incoherent")
    details = []
    scores = []
    candidate_operations = 0
    reference_operations = 0
    candidate_wins = 0
    reference_wins = 0
    draws = 0
    for index, pair in enumerate(pairs):
        left = results[2 * index]
        right = results[2 * index + 1]
        for result in (left, right):
            if result.get("runner_error") or result.get("exception") or result.get("ai_errors"):
                raise RuntimeError(f"combat invalide paire {index}: {json.dumps(result)[:8000]}")
        if pair.mode == "br":
            first_candidate = {1, 2, 3, 4, 5}
            second_candidate = {6, 7, 8, 9, 10}
            all_teams = set(range(1, 11))
            first = cohort_survival(left, first_candidate) - cohort_survival(
                left, all_teams - first_candidate
            )
            second = cohort_survival(right, second_candidate) - cohort_survival(
                right, all_teams - second_candidate
            )
            candidate_operations += cohort_operations(left, first_candidate)
            candidate_operations += cohort_operations(right, second_candidate)
            reference_operations += cohort_operations(left, all_teams - first_candidate)
            reference_operations += cohort_operations(right, all_teams - second_candidate)
            winner_tests = (
                (left, set(range(0, 5))),
                (right, set(range(5, 10))),
            )
        else:
            first = survival(left, 1) - survival(left, 2)
            second = survival(right, 2) - survival(right, 1)
            candidate_operations += operations(left, 1) + operations(right, 2)
            reference_operations += operations(left, 2) + operations(right, 1)
            winner_tests = ((left, {0}), (right, {1}))
        fitness = (first + second) / 2
        scores.append(fitness)
        for result, candidate_winners in winner_tests:
            if result["winner"] in candidate_winners:
                candidate_wins += 1
            elif result["winner"] >= 0:
                reference_wins += 1
            else:
                draws += 1
        details.append({
            "index": index,
            "seed": pair.seed,
            "left_ids": pair.left_ids,
            "right_ids": pair.right_ids,
            "fitness": fitness,
            "candidate_left": first,
            "candidate_right": second,
            "durations": [left["duration"], right["duration"]],
            "execution_time_ns": [left["execution_time_ns"], right["execution_time_ns"]],
        })
    mean = statistics.fmean(scores)
    standard_error = statistics.stdev(scores) / math.sqrt(len(scores)) if len(scores) > 1 else 0.0
    report = {
        "mean_fitness": mean,
        "standard_error": standard_error,
        "confidence_95": [mean - 1.96 * standard_error, mean + 1.96 * standard_error],
        "pairs": len(pairs),
        "fights": 2 * len(pairs),
        "candidate_wins": candidate_wins,
        "reference_wins": reference_wins,
        "draws": draws,
        # Diagnostic uniquement : des poids meilleurs peuvent allonger un match et donc
        # augmenter ce total sans rendre le scoring intrinsequement plus cher.
        "total_operations_diagnostic": {
            "candidate": candidate_operations,
            "reference": reference_operations,
            "ratio": candidate_operations / reference_operations if reference_operations else None,
        },
        "details": details,
    }
    if profile_ops:
        report["intrinsic_ops_profile"] = summarize_profile(results, pairs[0].mode)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("farmer", "solo", "br"), default="farmer")
    parser.add_argument("--pairs", type=int, default=2)
    parser.add_argument("--max-turns", type=int, default=3)
    parser.add_argument("--selection-seed", type=int, default=20260812)
    parser.add_argument("--min-cores", type=int, default=1024)
    parser.add_argument("--jobs", type=int, choices=(1, 2), default=1)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--profile-ops", action="store_true")
    parser.add_argument("--model-vector", type=Path)
    parser.add_argument("--transition-vector", type=Path)
    parser.add_argument("--bias", action="append", default=[])
    parser.add_argument("--transition", action="append", default=[])
    parser.add_argument("--index", action="append", default=[])
    parser.add_argument("--name", default="candidate")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--generator", type=Path, default=DEFAULT_GENERATOR)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    # Les copies candidates vivent sous le générateur et doivent ensuite être rendues
    # relatives à sa racine pour NativeFileSystem. Normaliser ici évite qu'un --generator
    # relatif échoue dans Path.relative_to malgré deux chemins désignant le même dossier.
    args.generator = args.generator.resolve()
    args.data = args.data.resolve()

    biases = parse_assignments(args.bias, allowed_names=True)
    transition_biases = parse_transition_assignments(args.transition)
    indices = parse_assignments(args.index, allowed_names=False)
    replacement = vector_file(args.model_vector) if args.model_vector else None
    transition_replacement = (
        transition_vector_file(args.transition_vector) if args.transition_vector else None
    )
    solo, farmers, builds = load_records(args.data)
    pairs = make_pairs(args.mode, args.pairs, args.selection_seed, solo, farmers, builds)
    java_home = resolve_java_home(args.generator, args.java_home)
    compile_runner(args.generator, java_home)

    started = time.monotonic()
    fingerprint = {
        "mode": args.mode,
        "pairs": args.pairs,
        "max_turns": args.max_turns,
        "selection_seed": args.selection_seed,
        "min_cores": args.min_cores,
        "batch_size": args.batch_size,
        "profile_ops": args.profile_ops,
        "biases": biases,
        "transition_biases": transition_biases,
        "indices": indices,
        "candidate_model_sha256": (
            file_sha256(args.model_vector) if args.model_vector else None
        ),
        "candidate_transition_sha256": (
            file_sha256(args.transition_vector) if args.transition_vector else None
        ),
        "data_sha256": file_sha256(args.data),
        "model_sha256": file_sha256(ROOT / "New_AI/Scoring/NNModel.leek"),
        "generator_sha256": file_sha256(args.generator / "generator.jar"),
    }
    checkpoint = (
        args.output.with_suffix(args.output.suffix + ".partial") if args.output else None
    )
    # NativeFileSystem interdit volontairement les chemins qui sortent de sa racine (cwd du
    # generateur). Les copies temporaires vivent donc sous cette racine et les scenarios leur
    # passent des chemins relatifs.
    with tempfile.TemporaryDirectory(prefix=".leekwars-training-", dir=args.generator) as temporary:
        workspace = Path(temporary)
        candidate_dir = workspace / "candidate"
        reference_dir = workspace / "reference"
        shutil.copytree(ROOT / "New_AI", candidate_dir)
        shutil.copytree(ROOT / "New_AI", reference_dir)
        patch_model(candidate_dir, biases, indices, replacement)
        patch_transition_model(candidate_dir, transition_biases, transition_replacement)
        if args.profile_ops:
            enable_profiler(candidate_dir)
            enable_profiler(reference_dir)
        candidate_ai = (candidate_dir / "Main.leek").relative_to(args.generator)
        reference_ai = (reference_dir / "Main.leek").relative_to(args.generator)
        scenario_paths = []
        for index, pair in enumerate(pairs):
            for offset, candidate_team in enumerate((1, 2)):
                path = workspace / f"scenario-{index:05d}-{offset}.json"
                write_scenario(
                    path, pair, builds, candidate_ai, reference_ai,
                    candidate_team, args.max_turns, args.min_cores,
                )
                scenario_paths.append(path)

        work = chunks(scenario_paths, args.batch_size)
        completed_batches: dict[int, list[dict[str, Any]]] = {}
        if checkpoint and checkpoint.exists():
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            if saved.get("fingerprint") == fingerprint:
                completed_batches = {
                    int(index): values for index, values in saved.get("batches", {}).items()
                }
                print(f"reprise: {len(completed_batches)}/{len(work)} batches", flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            future_to_index = {
                pool.submit(
                    run_batch, batch, args.generator, java_home, args.timeout, args.profile_ops
                ): index
                for index, batch in enumerate(work)
                if index not in completed_batches
            }
            for future in concurrent.futures.as_completed(future_to_index):
                index = future_to_index[future]
                completed_batches[index] = future.result()
                if checkpoint:
                    write_json_atomic(
                        checkpoint,
                        {
                            "fingerprint": fingerprint,
                            "batches": {str(key): value for key, value in completed_batches.items()},
                        },
                    )
                print(f"batch {len(completed_batches)}/{len(work)}", flush=True)
        results = [item for index in range(len(work)) for item in completed_batches[index]]

    report = {
        "name": args.name,
        "mode": args.mode,
        "max_turns": args.max_turns,
        "selection_seed": args.selection_seed,
        "min_cores": args.min_cores,
        "biases": biases,
        "transition_biases": transition_biases,
        "indices": indices,
        "candidate_model_sha256": fingerprint["candidate_model_sha256"],
        "candidate_transition_sha256": fingerprint["candidate_transition_sha256"],
        "data_sha256": fingerprint["data_sha256"],
        "model_sha256": fingerprint["model_sha256"],
        "generator_sha256": fingerprint["generator_sha256"],
        "elapsed_seconds": time.monotonic() - started,
        **summarize(pairs, results, args.profile_ops),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        write_json_atomic(args.output, report)
        if checkpoint:
            checkpoint.unlink(missing_ok=True)
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

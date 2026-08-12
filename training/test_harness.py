#!/usr/bin/env python3

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from run_benchmark import (
    DEFAULT_DATA,
    MODEL_BIASES,
    PairSpec,
    load_records,
    make_pairs,
    model_vector,
    patch_model,
    resolve_java_home,
    profile_lines,
    survival,
    vector_file,
    write_scenario,
)


ROOT = Path(__file__).resolve().parents[1]


class TrainingHarnessTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.solo, cls.farmers, cls.builds = load_records(DEFAULT_DATA)

    def test_model_layout_matches_documented_indices(self) -> None:
        vector, _, _ = model_vector(ROOT / "New_AI/Scoring/NNModel.leek")
        self.assertEqual(976, len(vector))
        self.assertEqual(len(MODEL_BIASES), len(set(MODEL_BIASES.values())))
        self.assertTrue(all(0 <= index < len(vector) for index in MODEL_BIASES.values()))

    def test_model_patch_changes_only_requested_parameters(self) -> None:
        source = ROOT / "New_AI/Scoring/NNModel.leek"
        original, _, _ = model_vector(source)
        with tempfile.TemporaryDirectory() as temporary:
            ai = Path(temporary)
            (ai / "Scoring").mkdir()
            shutil.copy2(source, ai / "Scoring/NNModel.leek")
            patch_model(ai, {"NET": -0.25}, {897: 3})
            patched, _, _ = model_vector(ai / "Scoring/NNModel.leek")
        changed = {index for index, values in enumerate(zip(original, patched)) if values[0] != values[1]}
        self.assertEqual({897, MODEL_BIASES["NET"]}, changed)
        self.assertEqual(-0.25, patched[MODEL_BIASES["NET"]])
        self.assertEqual(3, patched[897])

    def test_json_model_vector_can_be_benchmarked_without_editing_source(self) -> None:
        original, _, _ = model_vector(ROOT / "New_AI/Scoring/NNModel.leek")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "candidate.json"
            path.write_text(json.dumps({"vector": original}), encoding="utf-8")
            loaded = vector_file(path)
        self.assertEqual(original, loaded)

    def test_bundled_jdk_is_discovered_next_to_generator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generator = root / "leek-wars-generator"
            java_home = root / "tools/jdk"
            (java_home / "bin").mkdir(parents=True)
            (java_home / "bin/java").touch()
            (java_home / "bin/javac").touch()
            self.assertEqual(java_home, resolve_java_home(generator, None))

    def test_profile_filter_accepts_battle_royale_cohorts(self) -> None:
        result = {
            "entities": [
                {"id": 11, "team": 1, "summon": False},
                {"id": 61, "team": 6, "summon": False},
            ],
            "logs": {
                "farmer": {
                    "turn": [
                        [11, 0, "__PROFILE__|Scoring|120|3|1000"],
                        [61, 0, "__PROFILE__|Scoring|900|3|2000"],
                    ]
                }
            },
        }
        profile = profile_lines(result, {1, 2, 3, 4, 5})
        self.assertEqual([(3, 120)], profile["Scoring"])

    def test_public_dataset_is_complete_and_named(self) -> None:
        self.assertEqual(50, len(self.solo))
        self.assertEqual(50, len(self.farmers))
        self.assertEqual(215, len(self.builds))
        self.assertTrue(all(len(row["leek_ids"]) == 4 for row in self.farmers))
        for build in self.builds.values():
            for family in ("weapons", "chips", "components"):
                self.assertNotIn("unknown", (item["name"] for item in build[family]))

    def test_pair_selection_is_deterministic(self) -> None:
        first = make_pairs("farmer", 4, 1234, self.solo, self.farmers, self.builds)
        second = make_pairs("farmer", 4, 1234, self.solo, self.farmers, self.builds)
        self.assertEqual(first, second)
        self.assertTrue(all(len(pair.left_ids) == len(pair.right_ids) == 4 for pair in first))

    def test_farmer_scenario_exposes_mode_and_large_budget(self) -> None:
        pair = make_pairs("farmer", 1, 5678, self.solo, self.farmers, self.builds)[0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scenario.json"
            write_scenario(
                path, pair, self.builds, Path("candidate/Main.leek"),
                Path("reference/Main.leek"), 1, 6, 1024,
            )
            scenario = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(1, scenario["fight_type"])
        self.assertEqual(2, scenario["fight_context"])
        self.assertEqual([4, 4], [len(team) for team in scenario["entities"]])
        self.assertTrue(all(e["cores"] >= 1024 for team in scenario["entities"] for e in team))

    def test_battle_royale_has_ten_singleton_teams(self) -> None:
        pair = make_pairs("br", 1, 9012, self.solo, self.farmers, self.builds)[0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scenario.json"
            write_scenario(
                path, pair, self.builds, Path("candidate/Main.leek"),
                Path("reference/Main.leek"), 1, 3, 1024,
            )
            scenario = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(3, scenario["fight_type"])
        self.assertEqual(5, scenario["fight_context"])
        self.assertEqual(10, len(scenario["entities"]))
        self.assertTrue(all(len(team) == 1 for team in scenario["entities"]))
        candidate_paths = [team[0]["ai"] for team in scenario["entities"]]
        self.assertEqual(5, candidate_paths.count("candidate/Main.leek"))

    def test_survival_signal_is_bounded_by_initial_life(self) -> None:
        result = {
            "entities": [
                {
                    "team": 1, "summon": False, "initial_life": 1000,
                    "final_life": 1400,
                },
                {
                    "team": 1, "summon": False, "initial_life": 1000,
                    "final_life": 500,
                },
            ]
        }
        self.assertEqual(0.75, survival(result, 1))


if __name__ == "__main__":
    unittest.main()

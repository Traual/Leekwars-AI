#!/usr/bin/env python3
"""Collecte reproductible des builds publics du haut du classement Leek Wars.

Le script ne demande ni compte ni cookie. Chaque reponse est mise en cache avant
d'etre agregee afin qu'une coupure reseau ne fasse pas perdre la collecte.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


API = "https://leekwars.com/api"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GENERATOR = ROOT.parent / "leek-wars-generator"
DEFAULT_OUTPUT = ROOT / "training/data/meta_builds.jsonl"
DEFAULT_CACHE = ROOT / "training/data/.api-cache"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def request_json(route: str, cache: Path, refresh: bool, retries: int = 7) -> Any:
    target = cache / (route.replace("/", "__") + ".json")
    if target.exists() and not refresh:
        return read_json(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    error: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                f"{API}/{route}",
                headers={"User-Agent": "Leekwars-AI-training/1.0"},
            )
            with urllib.request.urlopen(req, timeout=90) as response:
                payload = json.loads(response.read())
            temporary = target.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(target)
            return payload
        except (OSError, ValueError, urllib.error.URLError) as exc:
            error = exc
            if attempt + 1 < retries:
                time.sleep(min(20.0, 1.25 * 2**attempt) + random.random())
    raise RuntimeError(f"API inaccessible apres {retries} essais: {route}: {error}")


def template_catalog(generator: Path) -> dict[str, dict[int, str]]:
    data = generator / "data"
    weapons = read_json(data / "weapons.json")
    chips = read_json(data / "chips.json")
    components = read_json(data / "components.json")
    return {
        "weapons": {int(value["item"]): value["name"] for value in weapons.values()},
        # L'API publique expose l'id de chip dans `template` (alors que les armes y
        # exposent l'id d'item). C'est aussi cet id que le scenario du generateur attend.
        "chips": {int(value["id"]): value["name"] for value in chips.values()},
        "components": {
            int(value["template"]): value["name"] for value in components.values()
        },
    }


def item_list(values: list[dict[str, Any]] | None, names: dict[int, str]) -> list[dict[str, Any]]:
    result = []
    for value in values or []:
        if not value:
            continue
        template = int(value["template"])
        result.append({"template": template, "name": names.get(template, "unknown")})
    return result


def build_record(leek: dict[str, Any], catalog: dict[str, dict[int, str]]) -> dict[str, Any]:
    stat_names = (
        "life", "strength", "wisdom", "agility", "resistance", "science",
        "magic", "frequency", "cores", "ram", "tp", "mp",
    )
    return {
        "kind": "build",
        "id": int(leek["id"]),
        "name": leek["name"],
        "level": int(leek["level"]),
        "talent": int(leek.get("talent", 0)),
        "farmer": leek.get("farmer"),
        # Les stats total_* comprennent capital et composants. Ce sont celles
        # qu'il faut injecter au generateur, pas les stats de base affichees par l'API.
        "stats": {
            name: int(leek.get(f"total_{name}", leek.get(name, 0)))
            for name in stat_names
        },
        # Le generateur attend les templates d'items publics (pas les ids d'inventaire).
        "weapons": item_list(leek.get("weapons"), catalog["weapons"]),
        "chips": item_list(leek.get("chips"), catalog["chips"]),
        "components": item_list(leek.get("components"), catalog["components"]),
        "source_url": f"https://leekwars.com/leek/{leek['id']}",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generator", type=Path, default=DEFAULT_GENERATOR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    catalog = template_catalog(args.generator)
    solo_payload = request_json(
        "ranking/get-active/leek/talent/1/null", args.cache, args.refresh
    )
    farmer_payload = request_json(
        "ranking/get-active/farmer/talent/1/null", args.cache, args.refresh
    )
    solo = solo_payload["ranking"][: args.limit]
    farmers = farmer_payload["ranking"][: args.limit]

    def fetch_farmer(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = request_json(f"farmer/get/{row['id']}", args.cache, args.refresh)
        return row, payload.get("farmer", payload)

    farmer_details: list[tuple[dict[str, Any], dict[str, Any]]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch_farmer, row) for row in farmers]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            farmer_details.append(future.result())
            print(f"farmers {index}/{len(futures)}", flush=True)
    farmer_details.sort(key=lambda pair: int(pair[0]["rank"]))

    farmer_records = []
    all_ids = {int(row["id"]) for row in solo}
    for row, detail in farmer_details:
        def local_rank(leek_id: int) -> tuple[int, int]:
            ranking = detail["leeks"][str(leek_id)].get("ranking")
            return (int(ranking) if ranking is not None else 999, leek_id)

        leeks = sorted(
            (int(leek_id) for leek_id in (detail.get("leeks") or {}).keys()),
            key=local_rank,
        )
        all_ids.update(leeks)
        farmer_records.append(
            {
                "kind": "farmer_ranking",
                "rank": int(row["rank"]),
                "id": int(row["id"]),
                "name": row["name"],
                "talent": int(row["talent"]),
                "leek_ids": leeks,
            }
        )

    def fetch_leek(leek_id: int) -> dict[str, Any]:
        payload = request_json(f"leek/get/{leek_id}", args.cache, args.refresh)
        return build_record(payload.get("leek", payload), catalog)

    builds = []
    ids = sorted(all_ids)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch_leek, leek_id) for leek_id in ids]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            builds.append(future.result())
            print(f"builds {index}/{len(futures)}", flush=True)
    builds.sort(key=lambda record: record["id"])

    solo_records = [
        {
            "kind": "solo_ranking",
            "rank": int(row["rank"]),
            "id": int(row["id"]),
            "name": row["name"],
            "talent": int(row["talent"]),
        }
        for row in solo
    ]
    manifest = {
        "kind": "manifest",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "source": API,
        "ranking": "active/talent",
        "solo_count": len(solo_records),
        "farmer_count": len(farmer_records),
        "unique_build_count": len(builds),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in [manifest, *solo_records, *farmer_records, *builds]:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(args.output)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""La ligue : les politiques adverses, resolues par EMPREINTE et jamais par nom de branche.

Une politique de ligue est soit un repertoire materialise (les dix versions de validation),
soit un commit du depot (l'ancre historique `scoring/champion-000`). Les deux sont resolus a
une empreinte complete de bundle, et c'est cette empreinte qui voyage ensuite : un nom de
branche bouge, une empreinte non.

L'ancre historique sert a suivre la DERIVE a long terme. Elle est resolue une fois vers son
commit, jamais relue depuis une reference mouvante.

Rappel du cahier des charges, a garder en tete dans tout rapport : le snapshot du classement
donne des BUILDS, pas les IA privees de ces joueurs. Les resultats se lisent « contre les
politiques de la ligue sur des builds representatifs », jamais « contre le vrai top 50 ».
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import bundle as mod_bundle

DEPOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Politique:
    ident: str
    origine: str          # "repertoire" ou "commit"
    source: str
    sha256: str
    nb_fichiers: int
    commit: str | None
    note: str = ""

    def chemin_deploiement(self, racine_generateur: Path) -> tuple[Path, str]:
        court = self.sha256[:16]
        return (Path(racine_generateur) / "test" / "ai" / "bundles" / court,
                "test/ai/bundles/%s/Main.leek" % court)

    def deployer(self, racine_generateur: Path) -> str:
        """Materialise le bundle sous la racine du generateur et rend le chemin RELATIF.

        Relatif, parce que le `NativeFileSystem` du compilateur resout depuis sa propre racine :
        un chemin absolu se compile sans erreur puis leve a chaque tour.
        """
        cible, relatif = self.chemin_deploiement(racine_generateur)
        if self.origine == "commit":
            mod_bundle.materialiser(self.commit, cible)
        else:
            mod_bundle.copier(Path(self.source), cible)
        if not (cible / "Main.leek").exists():
            raise FileNotFoundError("bundle sans Main.leek : %s" % cible)
        return relatif


def _resoudre_commit(ref: str) -> str:
    return subprocess.run(["git", "-C", str(DEPOT), "rev-parse", ref],
                          capture_output=True, text=True, check=True).stdout.strip()


def charger(source_versions: str | Path, ancre: str | None) -> list[Politique]:
    """Charge les politiques depuis le manifeste des versions, plus l'ancre historique."""
    source = Path(source_versions)
    politiques: list[Politique] = []
    if source.exists():
        manifeste = json.loads(source.read_text(encoding="utf-8"))
        racine = source.parent
        for v in manifeste.get("versions", []):
            rep = racine / v["id"]
            if not rep.is_dir():
                continue
            emp = mod_bundle.empreinte_repertoire(rep)
            politiques.append(Politique(v["id"], "repertoire", str(rep), emp["sha256"],
                                        emp["nb_fichiers"], None, v.get("politique", "")))
    if ancre:
        commit = _resoudre_commit(ancre)
        emp = mod_bundle.empreinte(commit)
        deja = any(p.sha256 == emp["sha256"] for p in politiques)
        if not deja:
            politiques.append(Politique("ancre:%s" % ancre, "commit", ancre, emp["sha256"],
                                        emp["nb_fichiers"], commit,
                                        "ancre historique, suit la derive a long terme"))
    if not politiques:
        raise ValueError("ligue vide : ni versions materialisees ni ancre resolue")
    return politiques


def resume(politiques: list[Politique]) -> list[dict]:
    return [{"id": p.ident, "origine": p.origine, "sha256": p.sha256,
             "nb_fichiers": p.nb_fichiers, "commit": p.commit, "note": p.note}
            for p in politiques]


def doublons(politiques: list[Politique]) -> list[list[str]]:
    """Politiques d'empreinte IDENTIQUE. Dix copies du meme code ne font pas dix adversaires,
    et le rapport doit le dire plutot que d'annoncer une diversite qui n'existe pas."""
    par_sha: dict[str, list[str]] = {}
    for p in politiques:
        par_sha.setdefault(p.sha256, []).append(p.ident)
    return [ids for ids in par_sha.values() if len(ids) > 1]

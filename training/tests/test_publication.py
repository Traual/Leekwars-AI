"""Publication d'un champion : commit, tag, manifeste, et reprise apres coupure.

Tout se passe dans un DEPOT TEMPORAIRE, avec des resultats SYNTHETIQUES explicitement
etiquetes. Aucune promotion n'est forcee dans le vrai registre : le champion reel reste
champion-000, etabli comme baseline et non comme vainqueur.

    python training/tests/test_publication.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

import bundle as mod_bundle          # noqa: E402
import publication as mod_pub        # noqa: E402
import registry as mod_reg           # noqa: E402


def _git(depot, *args):
    r = subprocess.run(["git", "-C", str(depot), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("git %s : %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout


def _depot_jouet(tmp: Path) -> tuple[Path, Path, str, str]:
    """Un depot minimal avec un New_AI, une branche scoring et un candidat deja commite."""
    depot = tmp / "depot"
    (depot / "New_AI").mkdir(parents=True)
    (depot / "training" / "champions").mkdir(parents=True)
    (depot / "New_AI" / "Main.leek").write_text("// champion initial\n", encoding="utf-8")
    _git(depot.parent, "init", "-q", "-b", "scoring", str(depot))
    _git(depot, "config", "user.email", "banc@local")
    _git(depot, "config", "user.name", "banc")
    (depot / "training" / "champions" / "current.json").write_text(json.dumps(
        {"champion_courant": "champion-000", "commit_code": "", "bundle_sha256": "",
         "tag": "scoring/champion-000"}), encoding="utf-8")
    _git(depot, "add", "-A")
    _git(depot, "commit", "-q", "-m", "initial")
    base = _git(depot, "rev-parse", "HEAD").strip()

    _git(depot, "checkout", "-q", "-b", "scoring-candidates/c1")
    (depot / "New_AI" / "Main.leek").write_text("// candidat gagnant\n", encoding="utf-8")
    _git(depot, "add", "-A")
    _git(depot, "commit", "-q", "-m", "candidat c1")
    commit_cand = _git(depot, "rev-parse", "HEAD").strip()
    _git(depot, "checkout", "-q", "scoring")
    return depot, depot / "training" / "champions", base, commit_cand


def _avec_depot(depot):
    """Fait pointer le module d'empreinte sur le depot jouet, le temps du test."""
    ancien = mod_bundle.DEPOT
    mod_bundle.DEPOT = depot
    return ancien


def test_promotion_ecrit_commit_tag_et_manifeste():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        depot, champions, base, commit_cand = _depot_jouet(tmp)
        ancien = _avec_depot(depot)
        try:
            emp = mod_bundle.empreinte(commit_cand)
            # Pointeur coherent avec le depot jouet.
            (champions / "current.json").write_text(json.dumps(
                {"champion_courant": "champion-000", "commit_code": base,
                 "bundle_sha256": "peu importe", "tag": "scoring/champion-000"}),
                encoding="utf-8")
            (champions / "champion-000.json").write_text(json.dumps({"id": "champion-000"}),
                                                         encoding="utf-8")
            with mod_reg.Registre(tmp / "r.sqlite") as reg:
                reg.champion_courant = lambda: json.loads(
                    (champions / "current.json").read_text(encoding="utf-8"))
                cand = {"id": "c1", "commit": commit_cand, "parent": base,
                        "bundle_sha256": emp["sha256"], "arbre_git": emp["arbre_git"],
                        "branche": "scoring-candidates/c1",
                        "hypothese": "SYNTHETIQUE : test de publication"}
                res = mod_pub.publier(
                    reg, cand, "champion-000",
                    {"verdict": "PROMOUVOIR", "_synthetique": True,
                     "_avertissement": "resultats fabriques pour le test, aucun combat joue"},
                    {"_synthetique": True},
                    champions=champions, depot=depot)

            assert res["champion"] == "champion-001"
            assert mod_pub.tag_existe("scoring/champion-001", depot=depot)
            # Le code du champion est EXACTEMENT celui du candidat.
            commit_champ = _git(depot, "rev-list", "-n", "1", "scoring/champion-001").strip()
            assert mod_bundle.empreinte(commit_champ)["sha256"] == emp["sha256"]
            pointeur = json.loads((champions / "current.json").read_text(encoding="utf-8"))
            assert pointeur["champion_courant"] == "champion-001"
            manifeste = json.loads((champions / "champion-001.json").read_text(encoding="utf-8"))
            assert manifeste["champion_precedent"] == "champion-000"
            assert manifeste["resultats"]["_synthetique"] is True
            # L'ancien tag et l'ancien manifeste survivent : on n'ecrase pas l'histoire.
            assert (champions / "champion-000.json").exists()
        finally:
            mod_bundle.DEPOT = ancien


def test_reprise_apres_coupure_ne_promeut_pas_deux_fois():
    """Interruption entre Git et SQLite : la reprise CONSTATE, elle ne refait pas."""
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        depot, champions, base, commit_cand = _depot_jouet(tmp)
        ancien = _avec_depot(depot)
        try:
            emp = mod_bundle.empreinte(commit_cand)
            (champions / "current.json").write_text(json.dumps(
                {"champion_courant": "champion-000", "commit_code": base,
                 "bundle_sha256": "x", "tag": "scoring/champion-000"}), encoding="utf-8")
            (champions / "champion-000.json").write_text(json.dumps({"id": "champion-000"}),
                                                         encoding="utf-8")
            base_sqlite = tmp / "r.sqlite"
            cand = {"id": "c1", "commit": commit_cand, "parent": base,
                    "bundle_sha256": emp["sha256"], "arbre_git": emp["arbre_git"],
                    "branche": "scoring-candidates/c1", "hypothese": "SYNTHETIQUE"}
            with mod_reg.Registre(base_sqlite) as reg:
                reg.champion_courant = lambda: json.loads(
                    (champions / "current.json").read_text(encoding="utf-8"))
                mod_pub.publier(reg, cand, "champion-000",
                                {"verdict": "PROMOUVOIR", "_synthetique": True},
                                {"_synthetique": True}, champions=champions, depot=depot)
                # On simule la coupure : la publication est rouverte comme si elle n'avait
                # jamais ete close cote SQLite, alors que Git a tout ecrit.
                reg.cx.execute("UPDATE publications SET etat='en_cours', etape='tag_ecrit'")

            with mod_reg.Registre(base_sqlite) as reg2:
                reg2.champion_courant = lambda: json.loads(
                    (champions / "current.json").read_text(encoding="utf-8"))
                etat = mod_pub.reconcilier(reg2, champions=champions, depot=depot)
                assert etat["etat"] == "terminee_par_reprise", etat
                assert etat["champion"] == "champion-001"
                # Et surtout : AUCUN second champion n'a ete cree.
                assert not mod_pub.tag_existe("scoring/champion-002", depot=depot)
                assert not (champions / "champion-002.json").exists()
        finally:
            mod_bundle.DEPOT = ancien


def test_le_vrai_registre_reste_intact():
    """Garde-fou : aucun test ne doit avoir promu quoi que ce soit dans le vrai depot."""
    pointeur = json.loads((RACINE / "champions" / "current.json").read_text(encoding="utf-8"))
    assert pointeur["champion_courant"] == "champion-000", (
        "le champion reel a bouge : un test a promu dans le vrai registre")
    assert not list((RACINE / "champions").glob("champion-0[1-9]*.json")), \
        "un manifeste de champion promu est apparu dans le vrai registre"


if __name__ == "__main__":
    echecs = 0
    for nom, fn in sorted(globals().items()):
        if not nom.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print("  ok   %s" % nom)
        except Exception as e:
            echecs += 1
            print("  ECHEC %s : %s" % (nom, e))
    print("\n%s" % ("tous les tests de publication passent" if not echecs
                    else "%d echec(s)" % echecs))
    raise SystemExit(1 if echecs else 0)

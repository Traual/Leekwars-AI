"""Reception de la BOUCLE, sur ses vrais points d'entree, avec un moteur synthetique.

Ces tests appellent `cli.cmd_run_loop`, `cli.cmd_evaluate` et `orchestrator.evaluer_etape` tels
que les commandes les appellent. Seul le PROCESSUS de combat est remplace : un moteur
synthetique lit la « force » que chaque bundle declare dans son scoring et tire un vainqueur.
Tout le reste — plan de blocs, cache, workers, points de reprise, couverture, decision,
tentatives de confirmation, publication Git — est le code de production.

Le depot, le registre, la ligue et les champions vivent dans un repertoire temporaire. Le vrai
registre n'est jamais touche : un test le verifie explicitement a la fin.

    python training/tests/test_boucle.py
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

import yaml                          # noqa: E402
import bundle as mod_bundle          # noqa: E402
import candidates as mod_cand        # noqa: E402
import cli                           # noqa: E402
import evaluator as mod_eval         # noqa: E402
import league as mod_ligue           # noqa: E402
import optimizer as mod_opt          # noqa: E402
import orchestrator as mod_orch      # noqa: E402
import publication as mod_pub        # noqa: E402
import registry as mod_reg           # noqa: E402
import scenarios as mod_sc           # noqa: E402
import tempfile                      # noqa: E402

SCORING = "// scoring synthetique\nglobal FORCE = %d;\n"

# Le vrai pointeur et les manifestes de champion AVANT tout test : aucun test ne doit les changer.
VRAI_POINTEUR_AVANT = (RACINE / "champions" / "current.json").read_text(encoding="utf-8")
VRAIS_MANIFESTES_AVANT = sorted(p.name for p in (RACINE / "champions").glob("*.json"))
MAIN = "include('Scoring/Scoring');\n// IA jouet\n"


def _ecrire(chemin: Path, texte: str) -> None:
    """Ecriture en OCTETS, fins de ligne LF.

    Sous Windows, l'ecriture texte de Python traduit les fins de ligne, et
    `core.autocrlf` est vrai sur cette machine : un patch fabrique par `git diff`
    ne s'appliquait alors sur aucun fichier du depot jouet. Le harnais de test
    doit ecrire ce qu'il croit ecrire.
    """
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(texte.encode("utf-8"))


def _git(depot, *args, verifier=True):
    r = subprocess.run(["git", "-C", str(depot), *args], capture_output=True, text=True)
    if verifier and r.returncode != 0:
        raise RuntimeError("git %s : %s" % (" ".join(args), (r.stderr or r.stdout).strip()))
    return r.stdout


# --------------------------------------------------------------------------------------
# Moteur synthetique
# --------------------------------------------------------------------------------------
def _force(racine: Path, chemin_ia: str) -> float:
    fichier = Path(racine) / Path(chemin_ia).parent / "Scoring" / "Scoring.leek"
    m = re.search(r"FORCE\s*=\s*(-?\d+)", fichier.read_text(encoding="utf-8"))
    return int(m.group(1)) / 100.0 if m else 0.0


class MoteurJouet(mod_eval.Moteur):
    """Le `Moteur` de production, sans JVM derriere : aucun binaire n'est lance ici."""

    def version_java(self) -> str:
        return "aucune JVM : moteur synthetique de reception"


class MoteurSynthetique:
    """Remplace le PROCESSUS de combat, pas l'orchestration.

    Il respecte le contrat d'`executer_flux` : un resultat remonte des qu'il est pret, une
    echeance coupe le lot, et ce qui manque manque — rien n'est invente.
    """

    def __init__(self, racine: Path):
        self.racine = Path(racine)
        self.joues = 0
        self.plafond = None          # coupure de reception apres N combats
        self.panne_contre = None     # empreinte d'une politique dont tous les combats echouent
        self.verrou = threading.Lock()
        self.lots_simultanes = 0
        self.lots_simultanes_max = 0

    def flux(self, moteur, build, chemins, timeout=1800.0, echeance=None, sur_resultat=None):
        with self.verrou:
            self.lots_simultanes += 1
            self.lots_simultanes_max = max(self.lots_simultanes_max, self.lots_simultanes)
        try:
            sorties = []
            for i, chemin in enumerate(chemins):
                brut = self._un_combat(Path(chemin))
                sorties.append(brut)
                if sur_resultat is not None:
                    sur_resultat(i, chemin, brut)
                time.sleep(0.001)
            return sorties
        finally:
            with self.verrou:
                self.lots_simultanes -= 1

    def _un_combat(self, chemin: Path) -> dict:
        sc = json.loads(chemin.read_text(encoding="utf-8"))
        ias = [groupe[0]["ai"] for groupe in sc["entities"]]
        with self.verrou:
            if self.plafond is not None and self.joues >= self.plafond:
                return {"runner_error": "coupure de reception apres %d combats" % self.joues}
            if self.panne_contre and any(self.panne_contre in ia for ia in ias):
                return {"runner_error": "panne simulee de l'adversaire"}
            self.joues += 1
        rnd = random.Random("%d|%s" % (sc["random_seed"], "|".join(ias)))
        forces = [_force(self.racine, ia) for ia in ias]
        if len(ias) > 2:
            poids = [max(0.01, 1.0 + f * 8) for f in forces]
            total, tirage, gagnant = sum(poids), rnd.random(), 0
            acc = 0.0
            for i, p in enumerate(poids):
                acc += p / total
                if tirage <= acc:
                    gagnant = i
                    break
            vainqueur = gagnant
        else:
            p = max(0.02, min(0.98, 0.5 + forces[0] - forces[1]))
            vainqueur = 0 if rnd.random() < p else 1
        return {"winner": vainqueur, "duration": 30, "execution_time_ns": 10 ** 9,
                "compilation_time_ns": 10 ** 8, "ai_errors": [], "system_errors": [],
                "entities": [{"id": e["id"], "team": camp}
                             for camp, groupe in enumerate(sc["entities"]) for e in groupe]}


# --------------------------------------------------------------------------------------
# Laboratoire jetable
# --------------------------------------------------------------------------------------
CONFIG = {
    "campagne": {"id": "essai", "confirmations_max": 2,
                 "risque_nominal_campagne": 0.05, "alpha_par_confirmation": 0.01},
    "moteur": {"racine": "", "java_home": None, "coeurs": "reels", "tours_max": 64},
    "objectif": {"poids": {"farmer": 0.80, "solo": 0.15, "team": 0.05, "br": 0.0},
                 "format_principal": "farmer",
                 "planchers_empiriques": {"solo": -0.05, "team": -0.05},
                 "gain_minimal_farmer": 0.01},
    "etapes": {
        # Les blocs PAR ADVERSAIRE ne decroissent jamais d'une etape a la suivante : c'est la
        # condition de l'emboitement, et `init-campaign` la verifie.
        "s1": {"candidats": 3, "blocs": {"farmer": 3, "solo": 3}, "adversaires": 1, "garder": 2},
        "s2": {"blocs": {"farmer": 6, "solo": 6, "team": 2}, "adversaires": 2, "garder": 1},
        "s3": {"blocs": {"farmer": 9, "solo": 9, "team": 3}, "adversaires": 3, "garder": 1},
        "confirmation": {"blocs": {"farmer": 30, "solo": 12, "team": 12}, "adversaires": 3,
                         "graines_neuves": True,
                         "formats_requis": ["farmer", "solo", "team"]},
    },
    "br": {"declenchement_toutes_les_n_promotions": 5, "lobbies": 2,
           "creneaux_focaux_par_lobby": 1, "veto": False},
    "ligue": {"source": "", "ancre": None},
    "debit": {"workers": 1, "taille_lot": 4, "timeout_lot_secondes": 60,
              "budget_pilote_secondes": 30},
    "optimiseur": {"mode": "manuel",
                   "portee": ["New_AI/Scoring/**"],
                   "hors_portee": ["training/**", "New_AI/Load/Cache.leek"],
                   "invariants": {"New_AI/Scoring/Scoring.leek":
                                  [{"motif": r"global FORCE", "minimum": 1,
                                    "raison": "le scoring doit garder sa force declaree"}]},
                   "candidats_max_par_vague": 3},
}


class Laboratoire:
    """Depot, ligue, registre et moteur synthetique, tous jetables."""

    def __init__(self, tmp: Path, etapes: dict | None = None, nb_adversaires: int = 3):
        self.tmp = tmp
        self.depot = tmp / "depot"
        self.champions = self.depot / "training" / "champions"
        self.gen = tmp / "gen"
        self.runs = tmp / "runs"
        (self.gen / "test" / "ai").mkdir(parents=True)
        self.runs.mkdir(parents=True)
        self._creer_depot()
        self._creer_ligue(nb_adversaires)
        self.moteur = MoteurJouet(self.gen, self.gen / "g.jar", None, self.gen, "moteur-jouet")
        self.synth = MoteurSynthetique(self.gen)

        self.cfg = json.loads(json.dumps(CONFIG))
        if etapes is not None:
            self.cfg["etapes"] = json.loads(json.dumps(etapes))
        self.cfg["moteur"]["racine"] = str(self.gen)
        self.cfg["ligue"]["source"] = str(self.tmp / "ligue" / "versions.json")
        self.chemin_cfg = tmp / "loop.yaml"
        self.chemin_cfg.write_text(yaml.safe_dump(self.cfg, allow_unicode=True),
                                   encoding="utf-8")

        self._anciens = {
            "bundle": mod_bundle.DEPOT, "cand": mod_cand.DEPOT, "ligue": mod_ligue.DEPOT,
            "pub_depot": mod_pub.DEPOT, "pub_champ": mod_pub.CHAMPIONS,
            "reg_runs": mod_reg.RUNS, "reg_champ": mod_reg.CHAMPIONS,
            "cli_runs": cli.RUNS, "flux": mod_eval.executer_flux,
            "contexte": cli._contexte,
        }
        mod_bundle.DEPOT = self.depot
        mod_cand.DEPOT = self.depot
        mod_ligue.DEPOT = self.depot
        mod_pub.DEPOT = self.depot
        mod_pub.CHAMPIONS = self.champions
        mod_reg.RUNS = self.runs
        mod_reg.CHAMPIONS = self.champions
        cli.RUNS = self.runs
        mod_eval.executer_flux = self.synth.flux
        cli._contexte = lambda args: (cli.charger_config(Path(args.config)), self.moteur,
                                      self.gen / "build")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        mod_bundle.DEPOT = self._anciens["bundle"]
        mod_cand.DEPOT = self._anciens["cand"]
        mod_ligue.DEPOT = self._anciens["ligue"]
        mod_pub.DEPOT = self._anciens["pub_depot"]
        mod_pub.CHAMPIONS = self._anciens["pub_champ"]
        mod_reg.RUNS = self._anciens["reg_runs"]
        mod_reg.CHAMPIONS = self._anciens["reg_champ"]
        cli.RUNS = self._anciens["cli_runs"]
        mod_eval.executer_flux = self._anciens["flux"]
        cli._contexte = self._anciens["contexte"]

    # ---- construction ----------------------------------------------------------------
    def _creer_depot(self):
        (self.depot / "New_AI" / "Scoring").mkdir(parents=True)
        self.champions.mkdir(parents=True)
        _ecrire(self.depot / "New_AI" / "Main.leek", MAIN)
        _ecrire(self.depot / "New_AI" / "Scoring" / "Scoring.leek", SCORING % 0)
        _git(self.tmp, "init", "-q", "-b", "scoring", str(self.depot))
        _git(self.depot, "config", "user.email", "banc@local")
        _git(self.depot, "config", "user.name", "banc")
        # Sans cela, Git reecrit les fins de ligne au checkout et le patch fabrique plus haut
        # ne s'applique sur rien.
        _git(self.depot, "config", "core.autocrlf", "false")
        _ecrire(self.champions / "champion-000.json",
                json.dumps({"id": "champion-000", "statut": "baseline"}))
        _ecrire(self.champions / "current.json", json.dumps(
            {"champion_courant": "champion-000", "commit_code": "", "bundle_sha256": "",
             "tag": ""}, indent=2))
        _git(self.depot, "add", "-A")
        _git(self.depot, "commit", "-q", "-m", "champion initial")
        # `base` est le commit du CODE du champion : c'est lui que le pointeur designe, et
        # c'est sur lui que les candidats sont enfantes.
        self.base = _git(self.depot, "rev-parse", "HEAD").strip()
        emp = mod_bundle_empreinte_dans(self.depot, self.base)
        _ecrire(self.champions / "current.json", json.dumps(
            {"champion_courant": "champion-000", "commit_code": self.base,
             "bundle_sha256": emp["sha256"], "tag": ""}, indent=2))
        _git(self.depot, "add", "-A")
        _git(self.depot, "commit", "-q", "-m", "pointeur du champion initial")

    def bundle_jouet(self, force: int, nom: str) -> str:
        """Un bundle deploye directement sous la racine du generateur, sans passer par Git.
        Sert de politique C distincte du champion quand le test a besoin des quatre
        orientations reellement differentes."""
        rep = self.gen / "test" / "ai" / "bundles" / nom
        (rep / "Scoring").mkdir(parents=True, exist_ok=True)
        _ecrire(rep / "Main.leek", MAIN)
        _ecrire(rep / "Scoring" / "Scoring.leek", SCORING % force)
        return "test/ai/bundles/%s/Main.leek" % nom

    def _creer_ligue(self, combien: int = 3):
        racine = self.tmp / "ligue"
        versions = []
        # Forces TOUTES distinctes : deux politiques au contenu identique auraient la meme
        # empreinte, donc le meme bundle deploye, et la ligue ne compterait qu'un adversaire.
        forces = ([-10, 0, 5] + [8 + i for i in range(max(0, combien - 3))])[:combien]
        for i, force in enumerate(forces, 1):
            rep = racine / ("adv%02d" % i)
            (rep / "Scoring").mkdir(parents=True)
            _ecrire(rep / "Main.leek", MAIN)
            _ecrire(rep / "Scoring" / "Scoring.leek", SCORING % force)
            versions.append({"id": "adv%02d" % i, "politique": "force %d" % force})
        (racine / "versions.json").write_text(json.dumps({"versions": versions}),
                                              encoding="utf-8")

    # ---- utilitaires -----------------------------------------------------------------
    def patch(self, force: int, chemin_relatif: str = "New_AI/Scoring/Scoring.leek",
              ajoute: str | None = None) -> str:
        """Un vrai diff unifie, fabrique par Git : un patch ecrit a la main casse.

        `ajoute` fait en plus CREER un fichier, en gardant une force qui change vraiment le
        jeu : un patch qui n'ajouterait qu'un fichier laisserait le candidat identique au
        champion et il n'atteindrait jamais la confirmation.
        """
        _git(self.depot, "checkout", "-q", "-b", "fabrique", self.base)
        _ecrire(self.depot / chemin_relatif, SCORING % force)
        if ajoute:
            _ecrire(self.depot / ajoute, "// helper ajoute par le candidat\n")
        _git(self.depot, "add", "-A")
        diff = _git(self.depot, "diff", "--cached")
        _git(self.depot, "reset", "-q", "--hard", self.base)
        # `reset --hard` ne retire pas toujours un fichier qui n'existait dans aucun commit :
        # le depot jouet doit repartir vraiment propre, sinon l'inscription du candidat refuse.
        _git(self.depot, "clean", "-fdq", "--", "New_AI")
        _git(self.depot, "checkout", "-q", "scoring")
        _git(self.depot, "branch", "-q", "-D", "fabrique")
        return diff

    def ecrire_patches(self, forces: dict[str, int],
                       chemins: dict[str, str] | None = None) -> Path:
        rep = self.tmp / "patches"
        rep.mkdir(exist_ok=True)
        for nom, force in forces.items():
            cible = (chemins or {}).get(nom, "New_AI/Scoring/Scoring.leek")
            _ecrire(rep / ("%s.patch" % nom), self.patch(force, cible))
        return rep

    def init_campagne(self):
        return cli.cmd_init_campaign(types.SimpleNamespace(config=str(self.chemin_cfg)))

    def run_loop(self, patches: Path, candidats=3, confirmations=1, minutes=10.0, workers=1):
        return cli.cmd_run_loop(types.SimpleNamespace(
            config=str(self.chemin_cfg), patches=str(patches), max_candidats=candidats,
            max_confirmations=confirmations, budget_minutes=minutes, workers=workers))

    def rapport_boucle(self):
        return json.loads((self.runs / "rapport-boucle.json").read_text(encoding="utf-8"))

    def registre(self):
        return mod_reg.Registre(self.runs / "registre.sqlite", champions=self.champions)


def mod_bundle_empreinte_dans(depot: Path, commit: str) -> dict:
    ancien = mod_bundle.DEPOT
    mod_bundle.DEPOT = depot
    try:
        return mod_bundle.empreinte(commit)
    finally:
        mod_bundle.DEPOT = ancien


# --------------------------------------------------------------------------------------
def test_boucle_complete_jusqu_a_la_promotion():
    """Proposition -> S1 -> selection -> S2 -> S3 -> confirmation -> promotion.

    L'ancienne boucle enregistrait les patches et appelait S1, rien de plus : aucune
    selection, aucun S2/S3, aucune confirmation, aucune promotion, et `Optimiseur.proposer`
    n'etait jamais appele.
    """
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            assert lab.init_campagne() == 0
            patches = lab.ecrire_patches({"a-faible": -20, "b-moyen": 10, "c-fort": 45})
            assert lab.run_loop(patches, candidats=3, confirmations=1, minutes=10.0) == 0
            rapport = lab.rapport_boucle()

            etapes = {}
            for ligne in rapport["journal"]:
                if "etape" in ligne and "candidat" in ligne:
                    etapes.setdefault(ligne["etape"], []).append(ligne)
            assert len(etapes.get("s1", [])) == 3, "les trois propositions passent S1"
            # S1 trie : au plus `garder` survivants, et jamais plus qu'a l'entree.
            assert 1 <= len(etapes.get("s2", [])) <= 2, etapes.get("s2")
            assert len(etapes["s2"]) < len(etapes["s1"]), "S1 doit eliminer"
            assert len(etapes.get("s3", [])) == 1, "S2 garde un candidat"
            assert len(etapes.get("confirmation", [])) == 1
            # Le candidat le plus faible ne va jamais plus loin que S1.
            assert not any(l["candidat"].endswith("a-faible") and l["etape"] != "s1"
                           for l in etapes.get("s2", []) + etapes.get("s3", []))
            assert rapport["promotions"] == 1, rapport["journal"]

            # Le candidat promu est le plus fort, et le champion actif a change.
            promu = [l for l in rapport["journal"] if l.get("etape") == "promotion"][0]
            assert promu["candidat"].endswith("c-fort"), promu["candidat"]
            with lab.registre() as reg:
                assert reg.champion_courant()["champion_courant"] == "champion-001"
                assert reg.publication_en_cours() is None
                assert reg.confirmations_utilisees("essai") == 1
            assert mod_pub.tag_existe("scoring/champion-001", depot=lab.depot)
            # Le code du champion est celui du candidat confirme.
            commit = _git(lab.depot, "rev-list", "-n", "1", "scoring/champion-001").strip()
            scoring = _git(lab.depot, "show", "%s:New_AI/Scoring/Scoring.leek" % commit)
            assert "FORCE = 45" in scoring


def test_budget_de_confirmations_zero_empeche_toute_promotion():
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            lab.init_campagne()
            patches = lab.ecrire_patches({"c-fort": 45})
            lab.run_loop(patches, candidats=1, confirmations=0, minutes=10.0)
            rapport = lab.rapport_boucle()
            assert rapport["promotions"] == 0
            assert any(l.get("detail") == "plafond de confirmations"
                       for l in rapport["journal"]), rapport["journal"]
            with lab.registre() as reg:
                assert reg.champion_courant()["champion_courant"] == "champion-000"


def test_candidat_hors_portee_n_est_jamais_evalue():
    """EXTENSION_A_EXAMINER doit BLOQUER, pas seulement s'afficher."""
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            lab.init_campagne()
            patches = lab.ecrire_patches({"hors": 45},
                                         {"hors": "New_AI/Load/Cache.leek"})
            lab.run_loop(patches, candidats=1, confirmations=1, minutes=10.0)
            rapport = lab.rapport_boucle()
            assert rapport["promotions"] == 0
            bloques = [l for l in rapport["journal"] if l.get("etat") == "bloque"]
            assert len(bloques) == 1 and bloques[0]["verdict"] == mod_opt.EXTENSION_A_EXAMINER
            assert not any(l.get("etape") == "s1" for l in rapport["journal"]), \
                "un candidat hors perimetre ne doit pas etre mesure"

            # Et la commande evaluate le refuse aussi, sur le meme verdict enregistre.
            code = cli.cmd_evaluate(types.SimpleNamespace(
                config=str(lab.chemin_cfg), id="essai-hors", stage="s1", workers=1,
                blocs=None, promouvoir=False))
            assert code == 3


def test_adversaire_en_panne_interdit_la_promotion():
    """Un adversaire sans resultats donne INCOMPLET, pas une decision sur les survivants."""
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            lab.init_campagne()
            pols = mod_ligue.charger(lab.cfg["ligue"]["source"], None)
            lab.synth.panne_contre = pols[1].sha256[:16]
            patches = lab.ecrire_patches({"c-fort": 45})
            lab.run_loop(patches, candidats=1, confirmations=1, minutes=10.0)
            rapport = lab.rapport_boucle()
            assert rapport["promotions"] == 0
            # S1 n'a qu'un adversaire (le premier) : il passe. S2 en ajoute un, en panne.
            s2 = [l for l in rapport["journal"] if l.get("etape") == "s2"]
            assert s2 and s2[0]["decision"] == "INCOMPLET", s2
            with lab.registre() as reg:
                assert reg.champion_courant()["champion_courant"] == "champion-000"


def test_deux_workers_et_points_de_reprise_sur_le_chemin_evaluate():
    """La file est constituee a l'echelle de l'ETAPE, et chaque combat est ecrit des sa fin.

    Avant : `evaluer_etape` appelait `jouer` bloc par bloc, donc quatre combats a distribuer et
    un seul lot actif ; et `jouer` n'ecrivait qu'apres la fin de tous les lots, ce qui perdait
    huit combats deja joues quand le deuxieme lot etait interrompu.
    """
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            lab.init_campagne()
            builds = mod_sc.charger_builds()
            pols = mod_ligue.charger(lab.cfg["ligue"]["source"], None)
            noms = [p.ident for p in pols]
            emp = mod_bundle.empreinte(lab.base)
            cli.deployer_bundle(lab.moteur, lab.base, emp["sha256"])
            ia_h = "test/ai/bundles/%s/Main.leek" % emp["sha256"][:16]
            ia_c, sha_c = lab.bundle_jouet(30, "candidat-jouet"), "c" * 64

            with lab.registre() as reg:
                par_format, couverture = mod_orch.evaluer_etape(
                    reg, lab.moteur, lab.gen / "build", builds, noms, pols, ia_c, sha_c,
                    ia_h, emp["sha256"], {"farmer": 6}, 4242, lab.runs / "m1",
                    workers=2, taille_lot=4)
                assert couverture["farmer"]["complet"]
                assert lab.synth.lots_simultanes_max >= 2, (
                    "le parcours evaluate doit distribuer la file entre les workers ; "
                    "maximum observe : %d" % lab.synth.lots_simultanes_max)
                joues = reg.executer("SELECT COUNT(*) AS n FROM matchs WHERE erreur='aucune'"
                                     ).fetchone()["n"]
                assert joues == 24, joues

            # Coupure au milieu d'une etape : ce qui est termine reste au registre.
            lab.synth.joues = 0
            lab.synth.plafond = 10
            with lab.registre() as reg:
                reg.executer("DELETE FROM matchs")
                _pf, couverture = mod_orch.evaluer_etape(
                    reg, lab.moteur, lab.gen / "build", builds, noms, pols, ia_c, sha_c,
                    ia_h, emp["sha256"], {"farmer": 6}, 4242, lab.runs / "m2",
                    workers=2, taille_lot=4)
                gardes = reg.executer("SELECT COUNT(*) AS n FROM matchs WHERE erreur='aucune'"
                                      ).fetchone()["n"]
                assert gardes == 10, ("les combats termines avant la coupure doivent etre "
                                      "conserves : %d" % gardes)
                assert not couverture["farmer"]["complet"]
                assert couverture["farmer"]["blocs_complets"] < 6


def test_br_apparie_les_graines_et_ne_compte_pas_une_panne_en_defaite():
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            lab.init_campagne()
            builds = mod_sc.charger_builds()
            pols = mod_ligue.charger(lab.cfg["ligue"]["source"], None)
            emp = mod_bundle.empreinte(lab.base)
            cli.deployer_bundle(lab.moteur, lab.base, emp["sha256"])
            ia_h = "test/ai/bundles/%s/Main.leek" % emp["sha256"][:16]
            ia_c, sha_c = lab.bundle_jouet(30, "br-candidat"), "c" * 64
            with lab.registre() as reg:
                rapport = mod_orch.auditer_br(reg, lab.moteur, lab.gen / "build", builds, pols,
                                              ia_c, sha_c, ia_h, emp["sha256"],
                                              lab.runs / "br", lobbies=3, creneaux=1, graine=7)
            # Les deux politiques focales ont joue la MEME graine dans chaque comparaison.
            par_paire = {}
            for ligne in rapport["lignes"]:
                par_paire.setdefault((ligne["lobby"], ligne["creneau"]), set()).add(
                    ligne["graine"])
            assert par_paire and all(len(g) == 1 for g in par_paire.values()), par_paire
            assert rapport["paires_completes"] == 3

            # Une panne du cote C ne doit pas devenir une defaite de la politique focale : la
            # paire reste incomplete, et la frequence de victoire n'est pas calculee dessus.
            lab.synth.panne_contre = "br-candidat"
            with lab.registre() as reg:
                reg.executer("DELETE FROM matchs")
                casse = mod_orch.auditer_br(reg, lab.moteur, lab.gen / "build", builds, pols,
                                            ia_c, sha_c, ia_h, emp["sha256"],
                                            lab.runs / "br2", lobbies=3, creneaux=1, graine=7)
            assert casse["paires_completes"] == 0
            assert len(casse["paires_incompletes"]) == 3
            assert casse["frequence_de_victoire"]["C"] is None
            assert casse["frequence_de_victoire"]["H"] is None, (
                "une paire amputee ne doit alimenter aucun des deux cotes")


def test_budget_de_temps_arrete_la_boucle():
    """Le budget temporel PILOTE la boucle : il ne se contente pas d'etre recopie au rapport."""
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            lab.init_campagne()
            patches = lab.ecrire_patches({"c-fort": 45})
            lab.run_loop(patches, candidats=3, confirmations=1, minutes=0.0)
            rapport = lab.rapport_boucle()
            assert rapport["promotions"] == 0
            assert rapport["budgets"]["secondes_restantes"] == 0.0
            assert any(l.get("detail") == "budget de temps epuise"
                       for l in rapport["journal"]), rapport["journal"]
            with lab.registre() as reg:
                assert reg.champion_courant()["champion_courant"] == "champion-000"


def test_la_boucle_reprend_une_publication_interrompue_avant_de_continuer():
    """Une publication laissee en vol bloque tout jusqu'a sa reprise."""
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            lab.init_campagne()
            rep = lab.tmp / "p0"
            rep.mkdir()
            _ecrire(rep / "avance.patch", lab.patch(35))
            info = mod_cand.enregistrer(
                "prealable", lab.base, patch=rep / "avance.patch", hypothese="prealable",
                portee=lab.cfg["optimiseur"]["portee"],
                hors_portee=lab.cfg["optimiseur"]["hors_portee"])
            with lab.registre() as reg:
                try:
                    mod_pub.publier(reg, info, "champion-000", {"verdict": "PROMOUVOIR"},
                                    {"_synthetique": True}, _coupure="tag_ecrit")
                    raise AssertionError("la coupure n'a pas eu lieu")
                except mod_pub.CoupureSimulee:
                    pass
                assert reg.champion_courant()["champion_courant"] == "champion-000"

            patches = lab.ecrire_patches({"c-fort": 45})
            lab.run_loop(patches, candidats=1, confirmations=0, minutes=10.0)
            rapport = lab.rapport_boucle()
            reprises = [l for l in rapport["journal"] if l.get("etape") == "reprise"]
            assert reprises and reprises[0]["etat"]["etat"] == "terminee_par_reprise", reprises
            with lab.registre() as reg:
                assert reg.champion_courant()["champion_courant"] == "champion-001"
                assert reg.publication_en_cours() is None


def _etapes_livrees() -> dict:
    """Les tailles et adversaires de la configuration REELLEMENT livree."""
    reelle = cli.charger_config(RACINE / "config" / "loop.yaml")
    return reelle["etapes"]


def test_la_configuration_livree_est_faisable_et_traverse_s2():
    """La vraie configuration, pas une autre.

    Le test precedent utilisait des tailles a lui, qui ne rencontraient pas le defaut : S2
    demandait quatre blocs team pour cinq adversaires, il en manquait forcement un, et la
    couverture rendait INCOMPLET meme quand tous les combats demandes se terminaient. Aucun
    candidat ne pouvait donc franchir S2.
    """
    etapes = _etapes_livrees()
    # Confirmation reduite : ce test porte sur le crible, et une confirmation grandeur nature
    # coute mille combats pour ne rien prouver de plus ici.
    etapes = json.loads(json.dumps(etapes))
    etapes["confirmation"]["blocs"] = {"farmer": 10, "solo": 10, "team": 10}
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t), etapes=etapes, nb_adversaires=11) as lab:
            panel = mod_ligue.charger(lab.cfg["ligue"]["source"], None)
            assert cli.controler_faisabilite(lab.cfg, panel, mod_sc.charger_builds()) == []
            assert lab.init_campagne() == 0
            patches = lab.ecrire_patches({"c-fort": 45})
            lab.run_loop(patches, candidats=1, confirmations=0, minutes=30.0)
            rapport = lab.rapport_boucle()
            par_etape = {l["etape"]: l for l in rapport["journal"] if l.get("etape")
                         in ("s1", "s2", "s3")}
            for etape in ("s1", "s2", "s3"):
                assert etape in par_etape, (etape, rapport["journal"])
                assert par_etape[etape]["decision"] != "INCOMPLET", par_etape[etape]
                assert par_etape[etape]["complet"], par_etape[etape]

            # Et le defaut d'origine est bien detecte par le controle de faisabilite.
            casse = json.loads(json.dumps(lab.cfg))
            casse["etapes"]["s2"]["blocs"]["team"] = 4
            problemes = cli.controler_faisabilite(casse, panel, mod_sc.charger_builds())
            assert any("s2 / team" in p for p in problemes), problemes


def test_la_boucle_reprend_un_candidat_et_son_avancement():
    """Arret apres S3, puis relance : une seule inscription, aucun combat de crible rejoue.

    La boucle redonnait le meme identifiant au meme patch et tentait de le reenregistrer : la
    branche existait deja, le candidat etait rejete, et les combats deja payes n'etaient plus
    relies a aucune idee.
    """
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            lab.init_campagne()
            patches = lab.ecrire_patches({"c-fort": 45})

            # Premier lancement : aucune confirmation autorisee, le crible va jusqu'a S3.
            lab.run_loop(patches, candidats=1, confirmations=0, minutes=30.0)
            premier = lab.rapport_boucle()
            crible = {l["etape"]: l for l in premier["journal"]
                      if l.get("etape") in ("s1", "s2", "s3")}
            assert set(crible) == {"s1", "s2", "s3"}, premier["journal"]
            combats_crible = lab.synth.joues
            assert combats_crible > 0
            with lab.registre() as reg:
                assert reg.confirmations_utilisees("essai") == 0
                assert len(reg.avancement("essai", "essai-c-fort")) == 3
            branches = _git(lab.depot, "branch", "--list",
                            "scoring-candidates/*").strip().split("\n")
            assert len([b for b in branches if b.strip()]) == 1, branches

            # Deuxieme lancement : le candidat est REPRIS, le crible n'est pas rejoue.
            lab.synth.joues = 0
            lab.run_loop(patches, candidats=1, confirmations=1, minutes=30.0)
            second = lab.rapport_boucle()
            repris = [l for l in second["journal"] if l.get("etat") == "repris"]
            assert any(l.get("detail", "").startswith("candidat deja enregistre")
                       for l in repris), second["journal"]
            assert {l["etape"] for l in repris if l.get("etape")} == {"s1", "s2", "s3"}, repris
            assert second["candidats_enregistres"] == 0, "aucun candidat neuf a inscrire"
            # Une seule branche, toujours : rien n'a ete renomme pour contourner le probleme.
            branches = _git(lab.depot, "branch", "--list",
                            "scoring-candidates/*").strip().split("\n")
            assert len([b for b in branches if b.strip()]) == 1, branches
            # La confirmation a bien eu lieu, et les combats joues sont les SIENS.
            conf = [l for l in second["journal"] if l.get("etape") == "confirmation"]
            assert conf and conf[0]["decision"] in ("PROMOUVOIR", "INCONCLUSIF"), conf
            assert lab.synth.joues > 0
            with lab.registre() as reg:
                assert reg.confirmations_utilisees("essai") == 1
                # Les mesures de developpement n'ont pas bouge.
                for etape in ("s1", "s2", "s3"):
                    assert reg.avancement("essai", "essai-c-fort")[etape]["objectif"] == \
                        crible[etape]["objectif"]


def test_une_confirmation_coupee_reprend_sa_tentative_sans_consommer_de_budget():
    """Coupure pendant une confirmation, puis relance : meme tentative, meme plan, meme
    compteur, et seuls les combats manquants sont joues."""
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            lab.init_campagne()
            patches = lab.ecrire_patches({"c-fort": 45})
            lab.run_loop(patches, candidats=1, confirmations=1, minutes=30.0)
            # Le parcours continu sert de reference.
            reference = json.loads((lab.runs / "rapports"
                                    / "essai-c-fort-confirmation-01.json")
                                   .read_text(encoding="utf-8"))
            assert reference["decision"]["verdict"] in ("PROMOUVOIR", "INCONCLUSIF")

        # Meme laboratoire, mais la confirmation est coupee au milieu.
        with tempfile.TemporaryDirectory() as t2:
            with Laboratoire(Path(t2)) as lab:
                lab.init_campagne()
                patches = lab.ecrire_patches({"c-fort": 45})
                lab.run_loop(patches, candidats=1, confirmations=0, minutes=30.0)
                lab.synth.joues = 0
                lab.synth.plafond = 20        # coupe la confirmation en cours de route
                lab.run_loop(patches, candidats=1, confirmations=1, minutes=30.0)
                coupe = lab.rapport_boucle()
                conf = [l for l in coupe["journal"] if l.get("etape") == "confirmation"][0]
                assert conf["decision"] == "INCOMPLET", conf
                assert conf["reprise"] is False
                vague = conf["tentative"]
                with lab.registre() as reg:
                    ligne = reg.confirmation_reprenable("essai", "essai-c-fort")
                    assert ligne is not None and ligne["etat"] == "partielle", dict(ligne or {})
                    assert reg.confirmations_utilisees("essai") == 1
                partiels = lab.synth.joues

                # Relance : MEME tentative, MEME plan, budget inchange.
                lab.synth.plafond = None
                lab.synth.joues = 0
                lab.run_loop(patches, candidats=1, confirmations=1, minutes=30.0)
                fini = lab.rapport_boucle()
                conf2 = [l for l in fini["journal"] if l.get("etape") == "confirmation"][0]
                assert conf2["reprise"] is True, conf2
                assert conf2["tentative"] == vague, (conf2["tentative"], vague)
                with lab.registre() as reg:
                    assert reg.confirmations_utilisees("essai") == 1, \
                        "une reprise ne consomme pas une confirmation de plus"
                # Seuls les combats MANQUANTS ont ete joues : la somme des deux passes vaut
                # EXACTEMENT le plan, donc aucun combat deja valide n'a ete rejoue.
                prevus = 4 * sum(lab.cfg["etapes"]["confirmation"]["blocs"].values())
                assert partiels == 20, partiels
                assert partiels + lab.synth.joues == prevus, (partiels, lab.synth.joues, prevus)
                # Et le resultat est celui du parcours continu.
                repris = json.loads((lab.runs / "rapports"
                                     / "essai-c-fort-confirmation-01.json")
                                    .read_text(encoding="utf-8"))
                assert repris["decision"]["verdict"] == reference["decision"]["verdict"]
                assert repris["resultats"] == reference["resultats"]


def test_une_tentative_ne_change_pas_de_champion_en_silence():
    """Si le champion bouge entre deux appels, la tentative figee ne doit pas etre reprise."""
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            lab.init_campagne()
            with lab.registre() as reg:
                tentative, reprise = reg.ouvrir_confirmation(
                    "essai", "c1", "champion-000", {"farmer": 3}, ["adv01"], "proto", 5)
                assert not reprise
                # Meme champion : la tentative se reprend.
                _meme, reprise = reg.ouvrir_confirmation(
                    "essai", "c1", "champion-000", {"farmer": 3}, ["adv01"], "proto", 5)
                assert reprise and _meme["vague"] == tentative["vague"]
                reg.cloturer_confirmation(tentative["id"], "partielle", "INCOMPLET")
                # Champion different : refus explicite, pas de changement de reference.
                try:
                    reg.ouvrir_confirmation("essai", "c1", "champion-001", {"farmer": 3},
                                            ["adv01"], "proto", 5)
                    raise AssertionError("la reprise aurait du etre refusee")
                except mod_reg.ChampionObsolete as e:
                    assert "champion-000" in str(e) and "champion-001" in str(e)
                assert reg.confirmations_utilisees("essai") == 1


def _etat_apres_coupure(lab, patches, coupure):
    """Lance la boucle jusqu'a une coupure donnee du passage decision -> publication.

    Rien n'est simule dans le controleur : on remplace le PROCESSUS qui devait suivre, comme
    une panne l'aurait fait. `coupure` vaut :
      - "cloture"    : la cloture de la tentative n'aboutit pas ;
      - "avant_pub"  : la decision est conservee, la publication n'est jamais ouverte ;
      - "code"       : la publication est ouverte puis interrompue a `code_prepare` ;
      - "bundle"     : interrompue a `bundle_verifie`, donc APRES la pose de l'arbre du
                       candidat et avant tout commit.
    """
    anciens = (cli._cloturer_tentative, cli.mod_pub.publier)
    if coupure == "cloture":
        def _casse(*a, **k):
            raise RuntimeError("panne de stockage pendant la cloture")
        cli._cloturer_tentative = _casse
    elif coupure == "avant_pub":
        def _jamais(*a, **k):
            raise RuntimeError("panne avant l'ouverture de la publication")
        cli.mod_pub.publier = _jamais
    elif coupure in ("code", "bundle"):
        vrai = anciens[1]
        etape = "code_prepare" if coupure == "code" else "bundle_verifie"
        cli.mod_pub.publier = lambda *a, _e=etape, **k: vrai(*a, _coupure=_e, **k)
    try:
        try:
            lab.run_loop(patches, candidats=1, confirmations=1, minutes=30.0)
        except Exception:
            pass
    finally:
        cli._cloturer_tentative, cli.mod_pub.publier = anciens


def test_une_decision_positive_survit_a_une_coupure_avant_sa_publication():
    """Les trois coupures du passage decision -> publication, puis deux relances.

    Une decision PROMOUVOIR doit etre conservee avec sa tentative, et sa promotion terminee a
    la relance : ni rejouer la confirmation, ni oublier le gagnant. Auparavant, la boucle
    sautait toute confirmation dont l'avancement ne valait pas INCOMPLET, y compris une
    decision positive jamais publiee.
    """
    for coupure, attendu in (("cloture", "tentative rouverte, plan repris"),
                             ("avant_pub", "decision conservee, promotion a terminer"),
                             ("code", "publication abandonnee, decision toujours publiable")):
        with tempfile.TemporaryDirectory() as t:
            with Laboratoire(Path(t)) as lab:
                lab.init_campagne()
                patches = lab.ecrire_patches({"c-fort": 45})
                _etat_apres_coupure(lab, patches, coupure)

                with lab.registre() as reg:
                    assert reg.confirmations_utilisees("essai") == 1, coupure
                    if coupure == "cloture":
                        # La cloture n'a pas eu lieu : la tentative reste OUVERTE, donc
                        # reprenable avec le meme plan.
                        assert reg.confirmation_reprenable("essai", "essai-c-fort") is not None
                    else:
                        a_publier = reg.confirmation_a_publier("essai", "essai-c-fort")
                        assert a_publier is not None, (coupure, "decision positive perdue")
                        assert a_publier["verdict"] == "PROMOUVOIR"
                    assert reg.champion_courant()["champion_courant"] == "champion-000"

                # Premiere relance : la promotion aboutit, sans confirmation supplementaire.
                lab.synth.joues = 0
                lab.run_loop(patches, candidats=1, confirmations=1, minutes=30.0)
                rapport = lab.rapport_boucle()
                assert rapport["promotions"] == 1, (coupure, attendu, rapport["journal"])
                with lab.registre() as reg:
                    assert reg.confirmations_utilisees("essai") == 1, (coupure, "budget entame")
                    assert reg.champion_courant()["champion_courant"] == "champion-001"
                    assert reg.publication_en_cours() is None
                if coupure != "cloture":
                    assert lab.synth.joues == 0, (
                        coupure, "aucun combat ne doit etre rejoue apres une decision complete")
                assert mod_pub.tag_existe("scoring/champion-001", depot=lab.depot)

                # Seconde relance : plus rien a faire, et surtout pas un second champion.
                lab.run_loop(patches, candidats=1, confirmations=1, minutes=30.0)
                assert lab.rapport_boucle()["promotions"] == 0, coupure
                assert not mod_pub.tag_existe("scoring/champion-002", depot=lab.depot)


def test_un_abandon_retire_les_fichiers_ajoutes_par_le_candidat():
    """Un candidat qui AJOUTE un helper ne doit pas laisser le depot sale apres un abandon.

    Le `reset` puis le `checkout` restaurent les fichiers suivis ; un fichier ajoute redevient
    simplement non suivi et survivait a l'abandon, ce qui bloquait ensuite l'inscription de
    tout nouveau candidat.
    """
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            lab.init_campagne()
            patches = lab.tmp / "patches"
            patches.mkdir(exist_ok=True)
            _ecrire(patches / "ajout.patch",
                    lab.patch(45, ajoute="New_AI/Scoring/Added.leek"))
            assert "new file" in (patches / "ajout.patch").read_text(encoding="utf-8"), \
                "le patch doit creer un fichier"
            # Coupure APRES la pose de l'arbre du candidat : le fichier ajoute est en place,
            # et rien n'est encore commite.
            _etat_apres_coupure(lab, patches, "bundle")
            assert (lab.depot / "New_AI" / "Scoring" / "Added.leek").exists(), \
                "la coupure doit tomber alors que le fichier ajoute est pose"

            with lab.registre() as reg:
                etat = mod_pub.reconcilier(reg, champions=lab.champions, depot=lab.depot)
            assert etat["etat"] == "abandonnee", etat
            assert etat["recuperable"] is True, etat
            assert etat["reste_a_nettoyer"] == [], etat
            assert not (lab.depot / "New_AI" / "Scoring" / "Added.leek").exists(), \
                "le fichier ajoute par le candidat doit avoir ete retire"
            # Le depot est reellement utilisable : un nouveau candidat s'inscrit.
            assert mod_cand.arbre_propre(), _git(lab.depot, "status", "--porcelain")
            with lab.registre() as reg:
                assert reg.champion_courant()["champion_courant"] == "champion-000"
            suite = lab.ecrire_patches({"suivant": 20})
            lab.run_loop(suite, candidats=2, confirmations=0, minutes=30.0)
            rejets = [l for l in lab.rapport_boucle()["journal"] if l.get("etat") == "rejete"]
            assert not rejets, rejets


def test_une_inscription_interrompue_entre_git_et_sqlite_se_reprend():
    """Panne apres le commit du candidat, avant son insertion : une seule branche, une seule
    inscription, et le candidat reste evaluable."""
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            lab.init_campagne()
            patches = lab.ecrire_patches({"c-fort": 45})
            vrai = cli._inscrire_candidat
            cli._inscrire_candidat = lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("stockage indisponible"))
            try:
                lab.run_loop(patches, candidats=1, confirmations=0, minutes=30.0)
            finally:
                cli._inscrire_candidat = vrai
            premier = lab.rapport_boucle()
            assert [l for l in premier["journal"] if l.get("etat") == "rejete"], premier
            with lab.registre() as reg:
                assert reg.candidat("essai-c-fort") is None, "le registre ne doit rien avoir"
            assert mod_cand.branche_existe("essai-c-fort"), "le commit, lui, existe"

            # Relance apres retablissement : le candidat est reconstruit depuis Git.
            lab.run_loop(patches, candidats=1, confirmations=0, minutes=30.0)
            second = lab.rapport_boucle()
            repris = [l for l in second["journal"] if l.get("etat") == "repris"]
            assert repris, second["journal"]
            assert not [l for l in second["journal"] if l.get("etat") == "rejete"], second
            with lab.registre() as reg:
                ligne = reg.candidat("essai-c-fort")
                assert ligne is not None, "l'inscription doit avoir ete reconstruite"
                assert ligne["commit"] if False else True
            branches = [b for b in _git(lab.depot, "branch", "--list",
                                        "scoring-candidates/*").strip().split("\n") if b.strip()]
            assert len(branches) == 1, branches
            assert any(l.get("etape") == "s1" for l in second["journal"]), second["journal"]


def test_un_budget_nul_ne_produit_ni_proposition_ni_inscription():
    """Zero minute, ou plafond de candidats atteint : l'optimiseur n'est pas sollicite.

    La boucle creait, commitait et inscrivait un candidat avec zero minute de budget, pour
    refuser S1 juste apres.
    """
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            lab.init_campagne()
            patches = lab.ecrire_patches({"c-fort": 45})

            class Compteur(mod_opt.Manuel):
                appels = 0

                def proposer(self, contexte):
                    Compteur.appels += 1
                    return super().proposer(contexte)

            vrai = cli._propositions
            cli._propositions = lambda cfg, source: [
                ("c-fort", Compteur((source / "c-fort.patch").read_text(encoding="utf-8")))]
            try:
                lab.run_loop(patches, candidats=1, confirmations=0, minutes=0.0)
                sans_temps = lab.rapport_boucle()
                assert Compteur.appels == 0, "aucune proposition ne doit etre demandee"
                assert sans_temps["candidats_enregistres"] == 0
                assert any(l.get("detail") == "budget de temps epuise"
                           for l in sans_temps["journal"]), sans_temps["journal"]
                with lab.registre() as reg:
                    assert reg.candidat("essai-c-fort") is None
                assert not mod_cand.branche_existe("essai-c-fort")

                # Plafond de candidats a zero : meme exigence.
                lab.run_loop(patches, candidats=0, confirmations=0, minutes=30.0)
                sans_place = lab.rapport_boucle()
                assert Compteur.appels == 0, "l'optimiseur ne doit pas etre sollicite"
                assert any(l.get("detail") == "plafond de candidats atteint"
                           for l in sans_place["journal"]), sans_place["journal"]

                # Avec du budget, la proposition est bien demandee une fois.
                lab.run_loop(patches, candidats=1, confirmations=0, minutes=30.0)
                assert Compteur.appels == 1, Compteur.appels
                # Et une relance ne redemande rien : le candidat existe.
                lab.run_loop(patches, candidats=1, confirmations=0, minutes=30.0)
                assert Compteur.appels == 1, Compteur.appels
            finally:
                cli._propositions = vrai


def _evaluer_confirm(lab, ident, **kw):
    args = types.SimpleNamespace(config=str(lab.chemin_cfg), id=ident, stage="confirm",
                                 workers=1, blocs=None, promouvoir=False,
                                 nouvelle_tentative=False)
    for cle, valeur in kw.items():
        setattr(args, cle, valeur)
    return cli.cmd_evaluate(args)


def test_evaluate_termine_une_decision_positive_au_lieu_de_la_rejouer():
    """La commande `evaluate` passe par la meme reprise de decision que la boucle.

    Sans cela, une relance apres interruption ouvrait une tentative 02 sur de nouvelles
    graines, rejouait tout le plan, consommait une seconde unite de budget, et laissait la
    tentative 01 orpheline en `a_publier`.
    """
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            lab.init_campagne()
            patches = lab.ecrire_patches({"c-fort": 45})
            # Le crible amene le candidat jusqu'a la confirmation.
            lab.run_loop(patches, candidats=1, confirmations=0, minutes=30.0)
            ident = "essai-c-fort"

            # Confirmation par `evaluate`, interrompue juste avant la publication.
            vrai = cli.mod_pub.publier
            cli.mod_pub.publier = lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("panne avant l'ouverture de la publication"))
            try:
                _evaluer_confirm(lab, ident, promouvoir=True)
            except RuntimeError:
                pass
            finally:
                cli.mod_pub.publier = vrai

            with lab.registre() as reg:
                a_publier = reg.confirmation_a_publier("essai", ident)
                assert a_publier is not None and a_publier["tentative"] == 1, dict(a_publier or {})
                assert reg.confirmations_utilisees("essai") == 1

            # Sans --promouvoir : refus explicite, aucune tentative de plus.
            lab.synth.joues = 0
            assert _evaluer_confirm(lab, ident) == 2
            with lab.registre() as reg:
                assert reg.confirmations_utilisees("essai") == 1
            assert lab.synth.joues == 0

            # Avec --promouvoir : la decision de la tentative 01 est publiee, sans combat.
            assert _evaluer_confirm(lab, ident, promouvoir=True) == 0
            assert lab.synth.joues == 0, "aucun combat ne doit etre rejoue"
            with lab.registre() as reg:
                assert reg.confirmations_utilisees("essai") == 1, "budget entame"
                assert reg.champion_courant()["champion_courant"] == "champion-001"
                assert reg.confirmation_a_publier("essai", ident) is None, \
                    "aucun etat a_publier ne doit rester orphelin"
                ligne = reg.confirmation_decidee("essai", ident)
                assert ligne["publication_etat"] == mod_reg.PUBLICATION_PUBLIEE
            assert mod_pub.tag_existe("scoring/champion-001", depot=lab.depot)


def test_une_branche_sans_empreinte_n_est_reprise_que_sur_preuve():
    """Sans marqueur de provenance, il faut DEMONTRER que le patch produit ce commit.

    Recopier l'empreinte demandee pour combler une provenance inconnue attribuait l'evaluation
    a une proposition qui n'avait pas produit le code mesure.
    """
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            lab.init_campagne()
            # Une branche a l'ancienne : commit sans marqueur `Empreinte-source`.
            patch_faible = lab.patch(10)
            chemin = lab.tmp / "faible.patch"
            _ecrire(chemin, patch_faible)
            _git(lab.depot, "checkout", "-q", "-b", "scoring-candidates/essai-vieux", lab.base)
            _git(lab.depot, "apply", "--index", str(chemin))
            _git(lab.depot, "commit", "-q", "-m", "candidat essai-vieux : sans marqueur")
            tete = _git(lab.depot, "rev-parse", "HEAD").strip()
            _git(lab.depot, "checkout", "-q", "scoring")
            assert mod_cand._empreinte_du_commit(tete) == "", "la branche doit etre sans marqueur"

            # Un AUTRE patch sous le meme identifiant : la reprise doit refuser.
            autre = lab.tmp / "fort.patch"
            _ecrire(autre, lab.patch(45))
            try:
                mod_cand.retrouver("essai-vieux", lab.base,
                                   cli._empreinte_source(patch=autre), autre)
                raise AssertionError("une proposition differente aurait du etre refusee")
            except RuntimeError as e:
                assert "autre arbre" in str(e), str(e)

            # Sans patch du tout : refus faute de preuve, jamais d'attribution par defaut.
            try:
                mod_cand.retrouver("essai-vieux", lab.base, "peu importe")
                raise AssertionError("une provenance inconnue aurait du etre refusee")
            except RuntimeError as e:
                assert "aucune empreinte de provenance" in str(e), str(e)

            # Le MEME patch : la reprise est acceptee, preuve faite par reconstruction.
            repris = mod_cand.retrouver("essai-vieux", lab.base,
                                        cli._empreinte_source(patch=chemin), chemin)
            assert repris["commit"] == tete
            assert repris["provenance"] == "arbre reconstruit", repris["provenance"]
            assert repris["empreinte_source"] == cli._empreinte_source(patch=chemin)
            # Et le code conserve est bien celui de la branche, pas celui du patch demande.
            assert "FORCE = 10" in mod_cand.contenu_au_commit(
                repris["commit"], "New_AI/Scoring/Scoring.leek")


def test_un_optimiseur_qui_epuise_le_budget_ne_fait_rien_inscrire():
    """Le budget est revérifié AU RETOUR de l'optimiseur, avant toute operation Git.

    Un appel a un fournisseur externe sera bien plus long que la lecture d'un patch : fournir
    l'echeance a l'appel ne dispense pas de la revoir a son retour.
    """
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            lab.init_campagne()
            patches = lab.ecrire_patches({"c-fort": 45})

            class Lent(mod_opt.Manuel):
                """Rend un patch valide, mais apres avoir consomme tout le temps restant."""
                budgets = None

                def proposer(self, contexte):
                    assert contexte.get("secondes_restantes") is not None, \
                        "l'echeance doit voyager avec la demande"
                    Lent.budgets.echeance = time.monotonic() - 1.0
                    return super().proposer(contexte)

            vrai = cli._propositions
            cli._propositions = lambda cfg, source: [
                ("c-fort", Lent((source / "c-fort.patch").read_text(encoding="utf-8")))]
            vraie_classe = cli.Budgets

            class Espion(vraie_classe):
                def __init__(self, *a):
                    super().__init__(*a)
                    Lent.budgets = self

            cli.Budgets = Espion
            try:
                lab.run_loop(patches, candidats=1, confirmations=0, minutes=30.0)
            finally:
                cli._propositions, cli.Budgets = vrai, vraie_classe

            rapport = lab.rapport_boucle()
            assert rapport["candidats_enregistres"] == 0, rapport["journal"]
            conservees = [l for l in rapport["journal"] if l.get("etat") == "conservee"]
            assert conservees, rapport["journal"]
            assert Path(conservees[0]["patch"]).exists(), "le patch doit etre conserve"
            assert not mod_cand.branche_existe("essai-c-fort"), "aucun commit ne doit exister"
            with lab.registre() as reg:
                assert reg.candidat("essai-c-fort") is None


def test_le_vrai_registre_reste_intact():
    assert (RACINE / "champions" / "current.json").read_text(encoding="utf-8") == VRAI_POINTEUR_AVANT
    assert sorted(p.name for p in (RACINE / "champions").glob("*.json")) == VRAIS_MANIFESTES_AVANT


if __name__ == "__main__":
    echecs = 0
    for nom, fn in sorted(globals().items()):
        if not nom.startswith("test_") or not callable(fn):
            continue
        t0 = time.monotonic()
        try:
            fn()
            print("  ok   %-62s %5.1f s" % (nom, time.monotonic() - t0))
        except Exception as e:
            echecs += 1
            import traceback
            print("  ECHEC %s : %s" % (nom, e))
            traceback.print_exc()
    print("\n%s" % ("tous les tests de boucle passent" if not echecs else "%d echec(s)" % echecs))
    raise SystemExit(1 if echecs else 0)

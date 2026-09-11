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
        "s1": {"candidats": 3, "blocs": {"farmer": 3, "solo": 3}, "adversaires": 1, "garder": 2},
        "s2": {"blocs": {"farmer": 6, "solo": 3, "team": 3}, "adversaires": 2, "garder": 1},
        "s3": {"blocs": {"farmer": 9, "solo": 3, "team": 3}, "adversaires": 3, "garder": 1},
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

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.depot = tmp / "depot"
        self.champions = self.depot / "training" / "champions"
        self.gen = tmp / "gen"
        self.runs = tmp / "runs"
        (self.gen / "test" / "ai").mkdir(parents=True)
        self.runs.mkdir(parents=True)
        self._creer_depot()
        self._creer_ligue()
        self.moteur = MoteurJouet(self.gen, self.gen / "g.jar", None, self.gen, "moteur-jouet")
        self.synth = MoteurSynthetique(self.gen)

        self.cfg = json.loads(json.dumps(CONFIG))
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

    def _creer_ligue(self):
        racine = self.tmp / "ligue"
        versions = []
        for i, force in enumerate((-10, 0, 5), 1):
            rep = racine / ("adv%02d" % i)
            (rep / "Scoring").mkdir(parents=True)
            _ecrire(rep / "Main.leek", MAIN)
            _ecrire(rep / "Scoring" / "Scoring.leek", SCORING % force)
            versions.append({"id": "adv%02d" % i, "politique": "force %d" % force})
        (racine / "versions.json").write_text(json.dumps({"versions": versions}),
                                              encoding="utf-8")

    # ---- utilitaires -----------------------------------------------------------------
    def patch(self, force: int, chemin_relatif: str = "New_AI/Scoring/Scoring.leek") -> str:
        """Un vrai diff unifie, fabrique par Git : un patch ecrit a la main casse."""
        _git(self.depot, "checkout", "-q", "-b", "fabrique", self.base)
        cible = self.depot / chemin_relatif
        _ecrire(cible, SCORING % force)
        _git(self.depot, "add", "-A")
        diff = _git(self.depot, "diff", "--cached")
        _git(self.depot, "reset", "-q", "--hard", self.base)
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


def test_le_vrai_registre_reste_intact():
    pointeur = json.loads((RACINE / "champions" / "current.json").read_text(encoding="utf-8"))
    assert pointeur["champion_courant"] == "champion-000"
    assert not list((RACINE / "champions").glob("champion-0[1-9]*.json"))


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

"""Publication d'un champion, et reprise apres coupure A CHAQUE FRONTIERE.

Tout se passe dans un DEPOT TEMPORAIRE, avec des resultats SYNTHETIQUES explicitement
etiquetes. Aucune promotion n'est forcee dans le vrai registre : le champion reel reste
champion-000, etabli comme baseline et non comme vainqueur.

La question a laquelle ces tests repondent n'est pas « la promotion marche-t-elle », mais
« une coupure peut-elle faire annoncer un champion qui n'est pas publie ». La reponse exigee
est non, a chacune des cinq frontieres.

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

SYNTHETIQUE = {"verdict": "PROMOUVOIR", "_synthetique": True,
               "_avertissement": "resultats fabriques pour le test, aucun combat joue"}


def _git(depot, *args):
    r = subprocess.run(["git", "-C", str(depot), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("git %s : %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout


def _depot_jouet(tmp: Path):
    """Un depot minimal avec un New_AI, une branche scoring et un candidat deja commite."""
    depot = tmp / "depot"
    (depot / "New_AI" / "Scoring").mkdir(parents=True)
    (depot / "training" / "champions").mkdir(parents=True)
    (depot / "New_AI" / "Main.leek").write_text("// champion initial\n", encoding="utf-8")
    (depot / "New_AI" / "Scoring" / "Obsolete.leek").write_text("// helper inutilise\n",
                                                                encoding="utf-8")
    _git(depot.parent, "init", "-q", "-b", "scoring", str(depot))
    _git(depot, "config", "user.email", "banc@local")
    _git(depot, "config", "user.name", "banc")
    champions = depot / "training" / "champions"
    (champions / "current.json").write_text(json.dumps(
        {"champion_courant": "champion-000", "commit_code": "", "bundle_sha256": "",
         "tag": "scoring/champion-000"}), encoding="utf-8")
    (champions / "champion-000.json").write_text(json.dumps({"id": "champion-000"}),
                                                 encoding="utf-8")
    _git(depot, "add", "-A")
    _git(depot, "commit", "-q", "-m", "initial")
    base = _git(depot, "rev-parse", "HEAD").strip()

    _git(depot, "checkout", "-q", "-b", "scoring-candidates/c1")
    (depot / "New_AI" / "Main.leek").write_text("// candidat gagnant\n", encoding="utf-8")
    # Le candidat RETIRE un helper de scoring : c'est exactement le cas que la preparation en
    # superposition cassait, en laissant le helper survivre dans l'arbre publie.
    (depot / "New_AI" / "Scoring" / "Obsolete.leek").unlink()
    _git(depot, "add", "-A")
    _git(depot, "commit", "-q", "-m", "candidat c1")
    commit_cand = _git(depot, "rev-parse", "HEAD").strip()
    _git(depot, "checkout", "-q", "scoring")

    # Le pointeur porte le commit de base, pour que le champion initial soit coherent.
    (champions / "current.json").write_text(json.dumps(
        {"champion_courant": "champion-000", "commit_code": base,
         "bundle_sha256": "peu importe", "tag": "scoring/champion-000"}), encoding="utf-8")
    _git(depot, "add", "-A")
    _git(depot, "commit", "-q", "-m", "pointeur initial")
    return depot, champions, base, commit_cand


class _Bac:
    """Un depot jouet, un registre a cote, et les modules pointes dessus le temps du test."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.depot, self.champions, self.base, self.commit_cand = _depot_jouet(tmp)
        self._anciens = (mod_bundle.DEPOT, mod_pub.DEPOT, mod_pub.CHAMPIONS)
        mod_bundle.DEPOT = self.depot
        mod_pub.DEPOT = self.depot
        mod_pub.CHAMPIONS = self.champions
        self.reg = mod_reg.Registre(tmp / "r.sqlite", champions=self.champions)
        self.candidat = {
            "id": "c1", "commit": self.commit_cand, "parent": self.base,
            "bundle_sha256": mod_bundle.empreinte(self.commit_cand)["sha256"],
            "arbre_git": mod_bundle.empreinte(self.commit_cand)["arbre_git"],
            "branche": "scoring-candidates/c1",
            "hypothese": "SYNTHETIQUE : test de publication"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.reg.fermer()
        mod_bundle.DEPOT, mod_pub.DEPOT, mod_pub.CHAMPIONS = self._anciens

    def publier(self, **kw):
        return mod_pub.publier(self.reg, self.candidat, "champion-000", SYNTHETIQUE,
                               {"_synthetique": True}, champions=self.champions,
                               depot=self.depot, **kw)

    def reconcilier(self):
        return mod_pub.reconcilier(self.reg, champions=self.champions, depot=self.depot)

    def champion_actif(self):
        return self.reg.champion_courant()["champion_courant"]

    def verifier_publication_complete(self, contexte=""):
        """Tout ce qu'une publication terminee doit laisser : tag, manifeste versionne,
        pointeur COMMITE, arbre propre, et le code exact du candidat."""
        assert self.champion_actif() == "champion-001", contexte
        assert mod_pub.tag_existe("scoring/champion-001", depot=self.depot), contexte
        commit = _git(self.depot, "rev-list", "-n", "1", "scoring/champion-001").strip()
        assert mod_bundle.empreinte(commit)["sha256"] == self.candidat["bundle_sha256"], contexte
        assert (self.champions / "champion-001.json").exists(), contexte
        assert _git(self.depot, "ls-tree", "--name-only", "scoring",
                    "training/champions/champion-001.json").strip(), contexte
        pointeur = mod_pub.pointeur_commite(self.depot)
        assert pointeur and pointeur["champion_courant"] == "champion-001", (contexte, pointeur)
        assert not _git(self.depot, "status", "--porcelain").strip(), contexte
        # Le helper que le candidat retirait doit reellement etre absent de l'arbre publie.
        assert not _git(self.depot, "ls-tree", "--name-only", "scoring",
                        "New_AI/Scoring/Obsolete.leek").strip(), contexte


def test_promotion_ecrit_commit_tag_manifeste_et_pointeur():
    with tempfile.TemporaryDirectory() as t:
        with _Bac(Path(t)) as bac:
            res = bac.publier()
            assert res["champion"] == "champion-001"
            bac.verifier_publication_complete()
            manifeste = json.loads((bac.champions / "champion-001.json")
                                   .read_text(encoding="utf-8"))
            assert manifeste["champion_precedent"] == "champion-000"
            assert manifeste["resultats"]["_synthetique"] is True
            # L'ancien manifeste survit : on n'ecrase pas l'histoire.
            assert (bac.champions / "champion-000.json").exists()


def test_un_candidat_qui_retire_un_helper_publie_un_arbre_identique_a_son_bundle():
    """La preparation en superposition laissait survivre les fichiers que le candidat retire.

    Le bundle mesure etait correct ; c'est sa preparation pour publication qui l'alterait, et
    la verification d'empreinte refusait ensuite une promotion pourtant legitime.
    """
    with tempfile.TemporaryDirectory() as t:
        with _Bac(Path(t)) as bac:
            # Le helper existe chez le champion, pas chez le candidat.
            assert _git(bac.depot, "ls-tree", "--name-only", "scoring",
                        "New_AI/Scoring/Obsolete.leek").strip()
            assert not _git(bac.depot, "ls-tree", "--name-only", bac.commit_cand,
                            "New_AI/Scoring/Obsolete.leek").strip()
            bac.publier()
            bac.verifier_publication_complete()
            assert not (bac.depot / "New_AI" / "Scoring" / "Obsolete.leek").exists(), \
                "le helper doit avoir disparu de l'arbre de travail aussi"


def test_coupure_a_chaque_frontiere_ne_change_jamais_le_champion_actif():
    """Sept coupures, sept fois la meme exigence : tant que la publication n'est pas close,
    le champion actif reste champion-000 — y compris quand le pointeur a deja ete ecrit.

    Les deux dernieres tombent APRES une ecriture Git reussie et avant la ligne SQLite qui la
    note : ce sont les fenetres ou le journal ment. `apres_commit` laissait la reprise conclure
    « aucun commit n'existait » alors que `scoring` portait deja le code du candidat, et
    `apres_pointeur` la faisait annoncer une publication terminee dont le pointeur versionne
    designait encore l'ancien champion.
    """
    attendus = {
        "code_prepare": "abandonnee", "bundle_verifie": "abandonnee",
        "commit_ecrit": "abandonnee", "apres_commit": "terminee_par_reprise",
        "tag_ecrit": "terminee_par_reprise", "pointeur_ecrit": "terminee_par_reprise",
        "apres_pointeur": "terminee_par_reprise",
    }
    assert set(attendus) == set(mod_pub.COUPURES), "une frontiere n'est pas couverte"
    for etape, attendu in attendus.items():
        with tempfile.TemporaryDirectory() as t:
            with _Bac(Path(t)) as bac:
                try:
                    bac.publier(_coupure=etape)
                    raise AssertionError("la coupure a %s n'a pas eu lieu" % etape)
                except mod_pub.CoupureSimulee:
                    pass
                assert bac.champion_actif() == "champion-000", (
                    "coupure a %s : le champion actif a bouge avant la cloture" % etape)
                en_vol = bac.reg.publication_en_cours()
                if etape == "apres_commit":
                    # La fenetre exacte : le commit EXISTE, son SHA n'est nulle part.
                    assert not (en_vol["commit_champion"] or ""), en_vol["commit_champion"]
                    assert not mod_pub.tag_existe("scoring/champion-001", depot=bac.depot)
                    assert _git(bac.depot, "ls-tree", "--name-only", "scoring",
                                "training/champions/champion-001.json").strip(), \
                        "le commit de champion doit bien exister sur la branche"
                if etape == "apres_pointeur":
                    # Le fichier annonce deja 001, le pointeur VERSIONNE dit encore 000.
                    assert json.loads((bac.champions / "current.json").read_text(
                        encoding="utf-8"))["champion_courant"] == "champion-001"
                    assert mod_pub.pointeur_commite(bac.depot)["champion_courant"] \
                        == "champion-000"
                etat = bac.reconcilier()
                assert etat["etat"] == attendu, (etape, etat)
                if attendu == "abandonnee":
                    assert bac.champion_actif() == "champion-000"
                    assert not mod_pub.tag_existe("scoring/champion-001", depot=bac.depot)
                    assert not (bac.champions / "champion-001.json").exists()
                    assert mod_pub.pointeur_commite(bac.depot)["champion_courant"] \
                        == "champion-000", "le pointeur versionne doit rester sur 000"
                    assert not _git(bac.depot, "status", "--porcelain").strip(), \
                        "l'arbre de travail doit etre restaure"
                    # Et le code du candidat ne doit pas etre reste sur la branche.
                    assert "candidat gagnant" not in _git(
                        bac.depot, "show", "scoring:New_AI/Main.leek")
                else:
                    bac.verifier_publication_complete(etape)
                # Dans les deux cas, une seule publication : jamais un second champion.
                assert not mod_pub.tag_existe("scoring/champion-002", depot=bac.depot)
                # Idempotence : une seconde reprise n'a plus rien a faire.
                assert bac.reconcilier()["etat"] == "rien_a_reconcilier", etape


def test_un_abandon_retire_ce_que_la_publication_a_cree_et_rien_d_autre():
    """Le nettoyage doit viser ce que la publication a cree, pas tout ce qui traine.

    Un fichier suivi se restaure par `checkout` ; un fichier AJOUTE par le candidat redevient
    simplement non suivi et survivait a l'abandon, laissant le depot sale. A l'inverse, un
    fichier etranger depose avant la publication n'est pas son affaire et doit survivre.
    """
    with tempfile.TemporaryDirectory() as t:
        with _Bac(Path(t)) as bac:
            # Le candidat AJOUTE un fichier au scoring.
            _git(bac.depot, "checkout", "-q", "scoring-candidates/c1")
            # Le candidat a retire le seul fichier de Scoring/ : Git a donc supprime le
            # repertoire au checkout.
            (bac.depot / "New_AI" / "Scoring").mkdir(parents=True, exist_ok=True)
            (bac.depot / "New_AI" / "Scoring" / "Ajoute.leek").write_text(
                "// helper ajoute par le candidat\n", encoding="utf-8")
            _git(bac.depot, "add", "-A")
            _git(bac.depot, "commit", "-q", "-m", "c1 ajoute un helper")
            bac.candidat["commit"] = _git(bac.depot, "rev-parse", "HEAD").strip()
            bac.candidat["bundle_sha256"] = mod_bundle.empreinte(
                bac.candidat["commit"])["sha256"]
            _git(bac.depot, "checkout", "-q", "scoring")

            # Un fichier ETRANGER, depose avant la publication.
            # Hors de `New_AI`, que la preparation du sous-arbre vide entierement : sous
            # `New_AI`, rien d'etranger ne peut survivre, et c'est ce qui rend l'arbre publie
            # exactement celui du candidat.
            etranger = bac.depot / "training" / "champions" / "brouillon-a-moi.txt"
            etranger.write_text("note personnelle\n", encoding="utf-8")

            try:
                bac.publier(_coupure="commit_ecrit")
                raise AssertionError("la coupure n'a pas eu lieu")
            except mod_pub.CoupureSimulee:
                pass
            assert (bac.depot / "New_AI" / "Scoring" / "Ajoute.leek").exists()

            etat = bac.reconcilier()
            assert etat["etat"] == "abandonnee", etat
            assert etat["recuperable"] is True, etat
            assert etat["reste_a_nettoyer"] == [], etat
            assert not (bac.depot / "New_AI" / "Scoring" / "Ajoute.leek").exists(), \
                "le fichier ajoute par le candidat doit avoir ete retire"
            assert etranger.exists(), "un fichier etranger a la publication doit survivre"
            assert bac.champion_actif() == "champion-000"
            # Hors ce fichier etranger, la zone de publication est propre.
            reste = _git(bac.depot, "status", "--porcelain", "--", "New_AI",
                         "training/champions").strip()
            assert reste == "?? training/champions/brouillon-a-moi.txt", repr(reste)


def test_bundle_refuse_ne_commite_rien():
    """Un bundle qui ne correspond pas a ce qui a ete evalue n'entre pas dans l'histoire."""
    with tempfile.TemporaryDirectory() as t:
        with _Bac(Path(t)) as bac:
            tete_avant = _git(bac.depot, "rev-parse", "scoring").strip()
            bac.candidat["bundle_sha256"] = "0" * 64
            try:
                bac.publier()
                raise AssertionError("un bundle different aurait du refuser la promotion")
            except RuntimeError as e:
                assert "differe de celui evalue" in str(e)
            assert bac.champion_actif() == "champion-000"
            etat = bac.reconcilier()
            assert etat["etat"] == "abandonnee", etat
            assert _git(bac.depot, "rev-parse", "scoring").strip() == tete_avant, \
                "aucun commit ne doit avoir ete ajoute a scoring"
            assert bac.champion_actif() == "champion-000"
            assert not (bac.champions / "champion-001.json").exists()


def test_reprise_ne_promeut_pas_deux_fois():
    """Interruption entre Git et SQLite : la reprise CONSTATE, elle ne refait pas."""
    with tempfile.TemporaryDirectory() as t:
        with _Bac(Path(t)) as bac:
            bac.publier()
            # On simule la coupure : la publication est rouverte comme si elle n'avait jamais
            # ete close cote SQLite, alors que Git a tout ecrit.
            bac.reg.executer("UPDATE publications SET etat='en_cours', etape='pointeur_ecrit'")
            etat = bac.reconcilier()
            assert etat["etat"] == "terminee_par_reprise", etat
            assert etat["champion"] == "champion-001"
            assert not mod_pub.tag_existe("scoring/champion-002", depot=bac.depot)
            assert not (bac.champions / "champion-002.json").exists()


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

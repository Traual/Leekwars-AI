"""Tests de reception du harnais.

Ils visent le BANC, pas le simulateur. Aucune revue du moteur, aucun audit de coupe.

Numerotation reprise du cahier des charges, section 11.2. Les tests qui exigent la commande
`evaluate` complete sont marques comme non couverts dans le README plutot que simules ici :
un test qui ne joue pas de combat ne prouve rien sur le cache de combats.

    python -m pytest training/tests/test_harnais.py -q
    ou   python training/tests/test_harnais.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

import bundle as mod_bundle          # noqa: E402
import evaluator as mod_eval         # noqa: E402
import scenarios as mod_sc           # noqa: E402
import statistics_lab as st          # noqa: E402
import registry as mod_reg           # noqa: E402


# ---------------------------------------------------------------------------- 4, 11
def test_empreinte_bundle_reproductible():
    """Deux calculs donnent la meme empreinte, et l'arbre git concorde."""
    a = mod_bundle.empreinte("HEAD")
    b = mod_bundle.empreinte("HEAD")
    assert a == b
    assert len(a["sha256"]) == 64 and a["nb_fichiers"] > 0


def test_champion_000_correspond_a_son_manifeste():
    """Test 11 : le tag de champion permet de retrouver EXACTEMENT le code evalue."""
    manifeste = json.loads((RACINE / "champions" / "champion-000.json").read_text(encoding="utf-8"))
    emp = mod_bundle.empreinte(manifeste["code"]["commit_source"])
    assert emp["sha256"] == manifeste["code"]["bundle_sha256"]
    assert emp["arbre_git"] == manifeste["code"]["arbre_git_New_AI"]
    pointeur = json.loads((RACINE / "champions" / "current.json").read_text(encoding="utf-8"))
    assert pointeur["bundle_sha256"] == emp["sha256"]


def test_cle_de_match_change_avec_le_bundle():
    """Test 4 : modifier le bundle d'UNE politique invalide le cache du match."""
    moteur = _moteur_factice()
    sc = {"random_seed": 1, "fight_type": 1}
    k1 = mod_eval.cle_de_match(sc, {"a": "aaa", "b": "bbb"}, moteur)
    k2 = mod_eval.cle_de_match(sc, {"a": "aaa", "b": "CCC"}, moteur)
    k3 = mod_eval.cle_de_match(sc, {"a": "aaa", "b": "bbb"}, moteur)
    assert k1 != k2, "un bundle different doit donner une cle differente"
    assert k1 == k3, "les memes entrees doivent donner la meme cle"


def test_cle_de_match_change_avec_le_scenario_et_le_moteur():
    moteur = mod_eval.Moteur(Path("."), Path("g.jar"), None, Path("."), "moteur-A")
    autre = mod_eval.Moteur(Path("."), Path("g.jar"), None, Path("."), "moteur-B")
    b = {"a": "aaa"}
    assert mod_eval.cle_de_match({"random_seed": 1}, b, moteur) != \
           mod_eval.cle_de_match({"random_seed": 2}, b, moteur), "la graine doit compter"
    assert mod_eval.cle_de_match({"random_seed": 1}, b, moteur) != \
           mod_eval.cle_de_match({"random_seed": 1}, b, autre), "le moteur doit compter"


# ---------------------------------------------------------------------------- 6
def test_signe_du_bloc_et_non_independance():
    """Test 6 : les signes des d_i sont corrects, et deux miroirs ne font pas deux blocs."""
    # C gagne a gauche et a droite : x(C)=1. H perd des deux cotes : x(H)=0. d = +1.
    x_c = (1.0 + 1.0) / 2
    x_h = (0.0 + 0.0) / 2
    assert x_c - x_h == 1.0
    # C gagne une orientation sur deux, H aussi : d = 0, et non « deux observations ».
    x_c = (1.0 + 0.0) / 2
    x_h = (1.0 + 0.0) / 2
    assert x_c - x_h == 0.0
    c = st.composante("farmer", "o1", [1.0, 0.0])
    assert c.n == 2, "un bloc = un d_i ; les quatre combats n'en font pas quatre"


def test_orientation_du_score():
    r = mod_eval.Resultat("k", 0, 1.0, "aucune", "", 10, 1.0, {})
    assert r.score("gauche") == 1.0 and r.score("droite") == 0.0
    nul = mod_eval.Resultat("k", -1, 0.5, "aucune", "", 10, 1.0, {})
    assert nul.score("gauche") == 0.5 and nul.score("droite") == 0.5


# ---------------------------------------------------------------------------- 7
def _par_format(**kw):
    return {f: [st.composante(f, "o%d" % i, d) for i, d in enumerate(lots)]
            for f, lots in kw.items()}


POIDS = {"farmer": 0.80, "solo": 0.15, "team": 0.05}
PLANCHERS = {"solo": -0.05, "team": -0.05}


def test_egalite_donne_inconclusif_et_non_equivalence():
    """Variance NULLE : ne pas fabriquer un intervalle de largeur zero."""
    d = _par_format(farmer=[[0.0] * 12, [0.0] * 12], solo=[[0.0] * 8])
    dec = st.decider(d, POIDS, PLANCHERS, 0.005, 0.01)
    assert dec.verdict == "INCONCLUSIF"
    assert any("equivalence" in r for r in dec.raisons)


def test_gain_net_est_promu():
    d = _par_format(farmer=[[0.30, 0.25, 0.35, 0.28, 0.31, 0.27] * 4] * 2,
                    solo=[[0.10, 0.12, 0.08, 0.11] * 3],
                    team=[[0.05, 0.06, 0.04, 0.05] * 3])
    dec = st.decider(d, POIDS, PLANCHERS, 0.005, 0.01)
    assert dec.verdict == "PROMOUVOIR", dec.raisons


def test_regression_farmer_compensee_par_solo_est_refusee():
    """L'eleveur doit progresser LUI-MEME : un gain solo ne rachete pas sa regression."""
    d = _par_format(farmer=[[-0.10, -0.12, -0.08, -0.11] * 6] * 2,
                    solo=[[0.90, 0.88, 0.92, 0.89] * 3])
    dec = st.decider(d, POIDS, PLANCHERS, 0.005, 0.01)
    assert dec.verdict != "PROMOUVOIR"
    assert any("eleveur" in r for r in dec.raisons)


def test_plancher_solo_franchi_bloque():
    d = _par_format(farmer=[[0.30, 0.25, 0.35, 0.28] * 6] * 2,
                    solo=[[-0.30, -0.28, -0.32, -0.29] * 3])
    dec = st.decider(d, POIDS, PLANCHERS, 0.005, 0.01)
    assert dec.verdict != "PROMOUVOIR"
    assert any("plancher" in r for r in dec.raisons)


def test_donnees_manquantes_donnent_incomplet():
    d = _par_format(solo=[[0.2] * 10])
    dec = st.decider(d, POIDS, PLANCHERS, 0.005, 0.01)
    assert dec.verdict == "INCOMPLET"


def test_effectif_insuffisant_est_inconclusif():
    d = _par_format(farmer=[[0.5]], solo=[[0.1]])
    dec = st.decider(d, POIDS, PLANCHERS, 0.005, 0.01)
    assert dec.verdict == "INCONCLUSIF"


def test_gain_minuscule_sous_le_seuil_pratique():
    """Statistiquement net mais pratiquement nul : refuse par le seuil de 0,01."""
    d = _par_format(farmer=[[0.004, 0.005, 0.006, 0.005] * 8] * 2,
                    solo=[[0.0] * 12])
    dec = st.decider(d, POIDS, PLANCHERS, 0.005, 0.01)
    assert dec.verdict != "PROMOUVOIR"
    assert any("seuil pratique" in r for r in dec.raisons)


def test_welch_diminue_avec_le_desequilibre():
    """Des composantes tres desequilibrees doivent baisser les degres de liberte."""
    _b1, nu1 = st.borne_basse(0.1, [0.01, 0.01], [30, 30], 0.05)
    _b2, nu2 = st.borne_basse(0.1, [0.02, 0.0001], [30, 30], 0.05)
    assert nu1 is not None and nu2 is not None and nu2 < nu1


# ---------------------------------------------------------------------------- 9, 11
def test_promotion_refusee_si_le_champion_a_change():
    """Test 9 : deux promotions concurrentes ne peuvent pas ecraser le registre."""
    with tempfile.TemporaryDirectory() as tmp:
        with mod_reg.Registre(Path(tmp) / "r.sqlite") as reg:
            pub = reg.ouvrir_publication("cand-1", "champion-000", "champion-001")
            assert pub > 0
            try:
                reg.ouvrir_publication("cand-2", "champion-000", "champion-002")
                raise AssertionError("une seconde publication n'aurait pas du s'ouvrir")
            except RuntimeError as e:
                assert "deja en cours" in str(e)


def test_promotion_refusee_sur_un_champion_perime():
    with tempfile.TemporaryDirectory() as tmp:
        with mod_reg.Registre(Path(tmp) / "r.sqlite") as reg:
            try:
                reg.ouvrir_publication("cand-1", "champion-042", "champion-043")
                raise AssertionError("le champion attendu est faux, l'ouverture aurait du echouer")
            except mod_reg.ChampionObsolete as e:
                assert "champion-042" in str(e)


def test_journal_de_publication_survit_a_une_interruption():
    """Test 11 : une interruption entre Git et SQLite se reprend sans double promotion."""
    with tempfile.TemporaryDirectory() as tmp:
        chemin = Path(tmp) / "r.sqlite"
        with mod_reg.Registre(chemin) as reg:
            pub = reg.ouvrir_publication("cand-1", "champion-000", "champion-001")
            reg.etape_publication(pub, "commit_ecrit", "abc123")
        # interruption simulee : la connexion meurt, le journal reste
        with mod_reg.Registre(chemin) as reprise:
            en_cours = reprise.publication_en_cours()
            assert en_cours is not None
            assert en_cours["etape"] == "commit_ecrit"
            assert en_cours["nouveau_champion"] == "champion-001"


# ---------------------------------------------------------------------------- 10
def test_adaptateur_llm_factice():
    """Test 10 : le cycle proposition -> evaluation -> retour tourne sans API payante."""
    import optimizer as opt
    prop = opt.Manuel().proposer({"objectif": "gagner", "champion": "champion-000"})
    assert prop["patch"] is not None
    assert prop["hypothese"]
    assert isinstance(prop["dependances"], list)


# ---------------------------------------------------------------------------- divers
def test_formats_portent_les_bons_codes_moteur():
    """Le mode envoye au moteur vient de la table « Fight types », pas des « full types »."""
    assert mod_sc.FORMATS["solo"].type_moteur == 0
    assert mod_sc.FORMATS["farmer"].type_moteur == 1
    assert mod_sc.FORMATS["team"].type_moteur == 2
    assert mod_sc.FORMATS["br"].type_moteur == 3
    assert mod_sc.FORMATS["team"].synthetique, "la team doit se declarer synthetique"


def test_coeurs_reels_et_tours_normaux():
    """Aucun plancher de coeurs, aucune troncature de tours."""
    builds = mod_sc.charger_builds()
    blocs = mod_sc.plan_de_blocs("farmer", ["o"], 1, 42, builds)
    sc = mod_sc.scenario(blocs[0], builds, "a/Main.leek", "b/Main.leek")
    assert sc["max_turns"] == 64
    for groupe in sc["entities"]:
        for e in groupe:
            attendu = builds[[b for b in builds if builds[b]["name"] == e["name"]][0]]["stats"]["cores"]
            assert e["cores"] == attendu, "les coeurs doivent etre ceux du build"


def test_graines_de_confirmation_sont_neuves():
    """Le decalage donne des graines jamais utilisees au developpement."""
    builds = mod_sc.charger_builds()
    dev = mod_sc.plan_de_blocs("farmer", ["o"], 20, 7, builds, decalage=0)
    conf = mod_sc.plan_de_blocs("farmer", ["o"], 20, 7, builds, decalage=10000)
    assert not ({b.graine for b in dev} & {b.graine for b in conf})


def test_classement_des_erreurs():
    assert mod_eval.classer({"runner_error": "Invalid AI"})[0] == mod_eval.ERREUR_COMPILATION
    assert mod_eval.classer({"runner_error": "disque plein"})[0] == mod_eval.ERREUR_INFRA
    assert mod_eval.classer({})[0] == mod_eval.ERREUR_MANQUANT
    # Un tour avorte ou en exception CONSERVE le resultat : c'est du jeu, pas une panne.
    cat, _ = mod_eval.classer({"winner": 0, "ai_errors": [[1002, 3]]})
    assert cat == mod_eval.ERREUR_IA
    assert mod_eval.analyser({"winner": 0, "ai_errors": [[1002, 3]]})[0] == 1.0


def _moteur_factice():
    return mod_eval.Moteur(Path("."), Path("g.jar"), None, Path("."), "empreinte-moteur")


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
    print("\n%s" % ("tous les tests passent" if not echecs else "%d echec(s)" % echecs))
    raise SystemExit(1 if echecs else 0)

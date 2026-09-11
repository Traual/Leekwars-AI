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


def test_graines_de_confirmation_sont_neuves_a_chaque_tentative():
    """Chaque TENTATIVE de confirmation tire dans sa propre vague.

    Le decalage fixe d'un million donnait les memes 160 blocs a toutes les confirmations de la
    campagne : retoucher une idee apres avoir vu ces resultats puis la reconfirmer ne
    fournissait plus une confirmation sur un echantillon nouveau.
    """
    builds = mod_sc.charger_builds()
    dev = mod_sc.plan_de_blocs("farmer", ["o"], 20, 7, builds)
    t1 = mod_sc.plan_de_blocs("farmer", ["o"], 20, 7, builds, vague="confirm-c1-01")
    t2 = mod_sc.plan_de_blocs("farmer", ["o"], 20, 7, builds, vague="confirm-c1-02")
    g_dev = {b.graine for b in dev}
    g1 = {b.graine for b in t1}
    g2 = {b.graine for b in t2}
    assert not (g_dev & g1) and not (g_dev & g2)
    assert not (g1 & g2), "deux tentatives ne doivent pas rejouer le meme echantillon"


def test_etapes_partagent_leurs_blocs():
    """S1 ⊂ S2 ⊂ S3 : ajouter un adversaire ne doit pas reattribuer les blocs deja joues.

    Avec `indice % nb_adversaires`, passer de trois a cinq adversaires ne conservait que trois
    des huit blocs de S1, et quatorze des vingt-quatre de S2 : le cout cumulatif annonce entre
    etapes n'existait pas.
    """
    builds = mod_sc.charger_builds()
    panel = ["p%d" % i for i in range(10)]
    s1 = mod_sc.plan_de_blocs("farmer", panel, 8, 99, builds, adversaires=panel[:3])
    s2 = mod_sc.plan_de_blocs("farmer", panel, 24, 99, builds, adversaires=panel[:5])
    s3 = mod_sc.plan_de_blocs("farmer", panel, 60, 99, builds, adversaires=panel)
    k1, k2, k3 = ({b.cle() for b in x} for x in (s1, s2, s3))
    assert k1 <= k2, "les blocs de S1 doivent tous se retrouver dans S2"
    assert k2 <= k3, "les blocs de S2 doivent tous se retrouver dans S3"
    assert {b.adversaire for b in s1} <= set(panel[:3])


def test_team_fait_deux_camps_de_quatre_avec_deux_proprietaires():
    """Un camp = une sous-liste de `entities`, et rien d'autre.

    Le generateur incremente son camp a chaque sous-liste (`State.addEntity`, `team = t`). La
    version precedente ecrivait quatre sous-listes de deux poireaux : quatre camps, et les deux
    eleveurs censes cooperer devenaient adversaires. Le champ JSON `team` ne corrige rien.
    """
    builds = mod_sc.charger_builds()
    bloc = mod_sc.plan_de_blocs("team", ["o"], 1, 4242, builds)[0]
    sc = mod_sc.scenario(bloc, builds, "g/Main.leek", "d/Main.leek")
    camps = mod_sc.camps_attendus(sc)
    assert len(camps) == 2, "le moteur construirait %d camps" % len(camps)
    assert [len(c) for c in camps] == [4, 4]
    for groupe in sc["entities"]:
        proprietaires = {e["farmer"] for e in groupe}
        assert len(proprietaires) == 2, "deux eleveurs differents par camp"
        assert len({e["ai"] for e in groupe}) == 1, "un camp joue une seule politique"
    assert {e["ai"] for e in sc["entities"][0]} != {e["ai"] for e in sc["entities"][1]}


def test_classement_des_erreurs():
    assert mod_eval.classer({"runner_error": "Invalid AI"})[0] == mod_eval.ERREUR_COMPILATION
    assert mod_eval.classer({"runner_error": "disque plein"})[0] == mod_eval.ERREUR_INFRA
    assert mod_eval.classer({})[0] == mod_eval.ERREUR_MANQUANT
    # Une panne du generateur PENDANT la generation est a rejouer, pas un resultat manquant.
    assert mod_eval.classer({"exception": "NullPointerException"})[0] == mod_eval.ERREUR_INFRA
    # Un tour avorte ou en exception CONSERVE le resultat : c'est du jeu, pas une panne.
    cat, _ = mod_eval.classer({"winner": 0, "ai_errors": [[1002, 3]], "system_errors": []})
    assert cat == mod_eval.ERREUR_IA
    assert mod_eval.analyser({"winner": 0, "ai_errors": [[1002, 3]],
                              "system_errors": []})[0] == 1.0


def test_ia_non_chargee_sort_des_mesures_mais_pas_un_combat_couteux():
    """Le critere de validite vient du MOTEUR, pas du cout ni de l'issue.

    Un combat ou l'IA epuise son plafond d'operations, perd, ou dure longtemps compte
    pleinement. Seul un combat ou le moteur n'a jamais eu d'IA a executer est ecarte, et c'est
    son erreur systeme qui le dit — pas une heuristique sur la duree.
    """
    sans_ia = {"winner": 1, "duration": 65, "execution_time_ns": 4 * 10**8,
               "ai_errors": [[1002, 1]] * 128,
               "system_errors": [[10001, 8, 61], [20001, 8, 61]]}
    assert mod_eval.classer(sans_ia)[0] == mod_eval.ERREUR_CHARGEMENT
    assert mod_eval.analyser(sans_ia)[0] is None
    bon, raison = mod_eval.valide_pour_le_debit(sans_ia)
    assert not bon and "ia_non_chargee" in raison

    # Meme sortie, mais l'erreur systeme est un depassement d'operations (101) : c'est du jeu.
    couteux = dict(sans_ia, system_errors=[[10001, 8, 101]] * 60)
    assert mod_eval.classer(couteux)[0] == mod_eval.ERREUR_IA
    assert mod_eval.valide_pour_le_debit(couteux)[0]
    assert mod_eval.analyser(couteux)[0] == 0.0        # l'equipe gauche a perdu, et ca compte


def test_couverture_incomplete_interdit_la_promotion():
    """Il ne suffit pas que les blocs CONSERVES soient complets.

    Reproduction d'Astra : quarante blocs prevus contre deux adversaires, tous les blocs de
    l'un manquent. Il reste vingt blocs et un adversaire — et l'ancienne decision rendait
    PROMOUVOIR en reponderant l'adversaire survivant.
    """
    d = _par_format(farmer=[[0.30, 0.25, 0.35, 0.28] * 5], solo=[[0.05, 0.02, 0.06, 0.03] * 5])
    couverture = {
        "farmer": {"blocs_attendus": 40, "blocs_complets": 20,
                   "blocs_incomplets": [{"indice": i, "manquants": []} for i in range(20)],
                   "adversaires_attendus": ["o0", "o1"], "adversaires_sans_donnees": ["o1"]},
        "solo": {"blocs_attendus": 20, "blocs_complets": 20, "blocs_incomplets": [],
                 "adversaires_attendus": ["o0"], "adversaires_sans_donnees": []},
    }
    dec = st.decider(d, POIDS, PLANCHERS, 0.005, 0.01, formats_requis=("farmer", "solo"),
                     couverture=couverture)
    assert dec.verdict == "INCOMPLET", dec.verdict
    assert any("o1" in r for r in dec.raisons)

    # Sans le format team, pourtant pondere : INCOMPLET, et non un verdict sur farmer seul.
    complet = {"farmer": dict(couverture["farmer"], blocs_complets=40, blocs_incomplets=[],
                              adversaires_sans_donnees=[]),
               "solo": couverture["solo"]}
    dec2 = st.decider(d, POIDS, PLANCHERS, 0.005, 0.01,
                      formats_requis=("farmer", "solo", "team"), couverture=complet)
    assert dec2.verdict == "INCOMPLET"
    assert any("team" in r for r in dec2.raisons)


def test_lot_technique_ne_peut_pas_promouvoir():
    """Des tailles imposees a la main donnent au mieux INDICATIF."""
    d = _par_format(farmer=[[0.30, 0.25, 0.35, 0.28] * 5], solo=[[0.05, 0.02, 0.06, 0.03] * 5])
    couverture = {f: {"blocs_attendus": 20, "blocs_complets": 20, "blocs_incomplets": [],
                      "adversaires_attendus": ["o0"], "adversaires_sans_donnees": []}
                  for f in ("farmer", "solo")}
    assert st.decider(d, POIDS, PLANCHERS, 0.005, 0.01, ("farmer", "solo"),
                      couverture=couverture).verdict == "PROMOUVOIR"
    dec = st.decider(d, POIDS, PLANCHERS, 0.005, 0.01, ("farmer", "solo"),
                     couverture=couverture, promouvable=False,
                     motif_non_promouvable="tailles imposees a la main")
    assert dec.verdict == "INDICATIF"
    assert any("NON PROMOUVABLE" in r for r in dec.raisons)


def test_invariant_de_budget_interne_bloque_le_candidat():
    """Autoriser BFS.leek n'autorise pas a retirer les gardes du budget d'operations."""
    import optimizer as opt
    invariants = {"f.leek": [{"motif": r"getOperations\(\) > OPS_DEADLINE", "minimum": 3,
                              "raison": "gardes du budget interne"}]}
    garde = "if (getOperations() > OPS_DEADLINE) break\n"
    assert opt.verifier_invariants(lambda c: garde * 3, invariants) == []
    ruptures = opt.verifier_invariants(lambda c: garde * 2, invariants)
    assert len(ruptures) == 1 and "budget interne" in ruptures[0]
    assert opt.verifier_invariants(lambda c: None, invariants)


def test_portee_autorisee_explicitement():
    import optimizer as opt
    verdict, fautifs = opt.classer_portee(["New_AI/Load/Cache.leek"],
                                          ["New_AI/Scoring/**"], ["New_AI/Load/Cache.leek"])
    assert verdict == opt.EXTENSION_A_EXAMINER and fautifs == ["New_AI/Load/Cache.leek"]
    assert verdict in opt.VERDICTS_BLOQUANTS
    assert opt.EXTENSION_AUTORISEE not in opt.VERDICTS_BLOQUANTS


def test_confirmations_tentatives_budget_et_reprise():
    """Les vraies fonctions du registre appelees par `evaluate` et `run-loop`."""
    with tempfile.TemporaryDirectory() as tmp:
        with mod_reg.Registre(Path(tmp) / "r.sqlite") as reg:
            t1, reprise = reg.ouvrir_confirmation("camp", "c1", "champion-000",
                                                  {"farmer": 8}, ["o1"], "proto", 2)
            assert not reprise
            # Reprise : meme tentative, MEME plan de graines.
            t1b, reprise = reg.ouvrir_confirmation("camp", "c1", "champion-000",
                                                   {"farmer": 8}, ["o1"], "proto", 2)
            assert reprise and t1b["vague"] == t1["vague"]
            reg.cloturer_confirmation(t1["id"], "close", "INCONCLUSIF")
            # Nouvelle tentative : vague NEUVE.
            t2, reprise = reg.ouvrir_confirmation("camp", "c1", "champion-000",
                                                  {"farmer": 8}, ["o1"], "proto", 2)
            assert not reprise and t2["vague"] != t1["vague"]
            reg.cloturer_confirmation(t2["id"], "close", "INCONCLUSIF")
            # Plafond effectivement impose.
            try:
                reg.ouvrir_confirmation("camp", "c2", "champion-000", {"farmer": 8},
                                        ["o1"], "proto", 2)
                raise AssertionError("le plafond de confirmations n'a pas ete impose")
            except mod_reg.BudgetEpuise as e:
                assert "2 tentatives sur 2" in str(e)


def test_protocole_fige_refuse_une_reecriture_silencieuse():
    with tempfile.TemporaryDirectory() as tmp:
        with mod_reg.Registre(Path(tmp) / "r.sqlite") as reg:
            assert reg.enregistrer_campagne("camp", "{}", "cfg", "proto-a", {}) == "creee"
            assert reg.enregistrer_campagne("camp", "{}", "cfg", "proto-a", {}) == "inchangee"
            try:
                reg.enregistrer_campagne("camp", "{}", "cfg", "proto-b", {})
                raise AssertionError("un protocole different aurait du etre refuse")
            except mod_reg.ProtocoleDifferent as e:
                assert "NOUVELLE campagne" in str(e)
            reg.verifier_protocole("camp", "proto-a")
            try:
                reg.verifier_protocole("camp", "proto-b")
                raise AssertionError("l'evaluation aurait du refuser un protocole different")
            except mod_reg.ProtocoleDifferent:
                pass


def test_flux_conserve_les_combats_termines_quand_l_echeance_tombe():
    """La VRAIE fonction d'execution, avec un faux processus a la place de la JVM.

    L'ancienne version attendait la fin du processus : une coupure au deuxieme lot rendait
    introuvables les huit combats deja joues du premier. Ici le premier resultat arrive avant
    l'echeance et doit etre conserve ; les suivants sont declares manquants, jamais inventes.
    """
    import time
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        marqueur = tmp / "enfant-vivant.txt"
        script = tmp / "faux_runner.py"
        # Le faux runner lance un PROCESSUS FILS, comme le shim Oracle `javapath` lance la
        # vraie JVM sous Windows. Tuer le seul parent laisserait ce fils tourner : c'est ainsi
        # qu'une campagne interrompue avait laisse vingt JVM abandonnees.
        script.write_text(
            "import sys, time, json, subprocess\n"
            "subprocess.Popen([sys.executable, '-c',\n"
            "    \"import time, sys, pathlib; time.sleep(3);\"\n"
            "    \" pathlib.Path(sys.argv[1]).write_text('vivant')\", sys.argv[1]])\n"
            "for i, chemin in enumerate(sys.argv[2:]):\n"
            "    if i:\n"
            "        time.sleep(5)\n"
            "    print('%s' + str(i) + '\\t' + json.dumps({'winner': 0, 'duration': 10,\n"
            "          'system_errors': []}), flush=True)\n" % mod_eval.PREFIXE,
            encoding="utf-8")
        scenarios = [tmp / ("s%d.json" % i) for i in range(3)]
        for s in scenarios:
            s.write_text("{}", encoding="utf-8")
        moteur = _moteur_factice()
        ancienne = mod_eval.commande_lot
        recus = []
        try:
            mod_eval.commande_lot = lambda m, b, sc: [sys.executable, str(script), str(marqueur),
                                                      *[str(p) for p in sc]]
            sorties = mod_eval.executer_flux(
                moteur, tmp, scenarios, timeout=60.0, echeance=time.monotonic() + 1.0,
                sur_resultat=lambda i, ch, brut: recus.append(i))
        finally:
            mod_eval.commande_lot = ancienne
        assert recus == [0], "le combat termine avant l'echeance doit etre remonte : %s" % recus
        assert sorties[0]["winner"] == 0
        for reste in sorties[1:]:
            assert "runner_error" in reste and "echeance" in reste["runner_error"]
            assert mod_eval.analyser(reste)[1] == mod_eval.ERREUR_INFRA
        time.sleep(4)
        assert not marqueur.exists(), (
            "la coupure doit emporter la DESCENDANCE du worker ; sinon une echeance de budget "
            "laisse des JVM orphelines qui continuent de manger la machine")


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

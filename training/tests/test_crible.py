"""Reception de l'EVALUATION PROGRESSIVE : paliers, arrets defavorables, reprise et cache.

Deux niveaux. Les regles d'arret sont testees sur des composantes construites a la main, ou
le resultat attendu se calcule. Le deroulement — combats non lances, cache partage, reprise,
blocs incomplets, confirmation — passe par `orchestrator.evaluer_etape_progressive` et
`cli.cmd_run_loop` tels quels, avec le moteur synthetique de `test_boucle` : seul le processus
de combat est remplace.

    python training/tests/test_crible.py
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
import tempfile
import time
import types
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bundle as mod_bundle          # noqa: E402
import cli                           # noqa: E402
import league as mod_ligue           # noqa: E402
import orchestrator as mod_orch      # noqa: E402
import progressif as mod_prog        # noqa: E402
import scenarios as mod_sc           # noqa: E402
import statistics_lab as st          # noqa: E402
from test_boucle import MAIN, SCORING, Laboratoire, _ecrire  # noqa: E402

# Parametres de crible de la campagne v3 : variances a priori mesurees sur campagne-003, alphas
# retenus au rejeu hors ligne de ses resultats complets.
CRIBLE = {
    "variance_a_priori": {"farmer": 0.0825, "solo": 0.0352, "team": 0.099},
    "ddl_a_priori": 4,
    "alpha_plancher": 0.10, "alpha_futilite": 0.30, "alpha_objectif": 0.10,
    "alpha_plancher_confirmation": 0.10, "alpha_futilite_confirmation": 0.10,
    "alpha_objectif_confirmation": 0.10,
    "seuil_premier_palier_farmer": -0.05,
    "seuil_objectif": 0.0,
}
OBJECTIF = {"poids": {"farmer": 0.80, "solo": 0.15, "team": 0.05, "br": 0.0},
            "format_principal": "farmer",
            "planchers_empiriques": {"solo": -0.05, "team": -0.05},
            "gain_minimal_farmer": 0.01}

# Plan d'une etape : un controle solo minuscule, un premier palier eleveur court, l'etape.
BLOCS = {"solo": 3, "farmer": 6}
PALIERS = [{"solo": 3}, {"solo": 3, "farmer": 3}, {"solo": 3, "farmer": 6}]
PREVUS = 4 * sum(BLOCS.values())

# Deux mondes synthetiques. Champion FORT (45) : face aux adversaires du laboratoire (-10, 0, 5)
# ses resultats sont presque certains, et la difference d'un candidat desastreux se lit sur trois
# blocs. Champion NEUTRE (0, le defaut) : un bon candidat (45) le domine nettement, ce qui rend
# sa poursuite certaine a l'echelle d'un palier.
FORCE_CHAMPION = 45


# --------------------------------------------------------------------------------------
# Outils
# --------------------------------------------------------------------------------------
def _bundle(lab: Laboratoire, nom: str, force: int, force_solo: int | None = None):
    rep = lab.gen / "test" / "ai" / "bundles" / nom
    (rep / "Scoring").mkdir(parents=True, exist_ok=True)
    _ecrire(rep / "Main.leek", MAIN)
    texte = SCORING % force
    if force_solo is not None:
        texte += "global FORCE_SOLO = %d;\n" % force_solo
    _ecrire(rep / "Scoring" / "Scoring.leek", texte)
    return "test/ai/bundles/%s/Main.leek" % nom, hashlib.sha256(nom.encode()).hexdigest()


class Etape:
    """Appelle l'orchestrateur comme `cli._evaluer`, et note les paliers effectivement juges."""

    def __init__(self, lab: Laboratoire, blocs=None, paliers=None, confirmation=False):
        self.lab = lab
        self.blocs = dict(blocs or BLOCS)
        self.paliers = mod_prog.paliers_de({"blocs": self.blocs, "paliers": paliers or PALIERS})
        self.confirmation = confirmation
        self.builds = mod_sc.charger_builds()
        self.pols = mod_ligue.charger(lab.cfg["ligue"]["source"], None)
        emp = mod_bundle.empreinte(lab.base)
        cli.deployer_bundle(lab.moteur, lab.base, emp["sha256"])
        self.ia_h = "test/ai/bundles/%s/Main.leek" % emp["sha256"][:16]
        self.sha_h = emp["sha256"]
        self.juges: list[int] = []

    def jouer(self, ia_c: str, sha_c: str, travail: str = "m", arret_impose: str | None = None):
        """`arret_impose` remplace la regle par un arret au premier palier juge : pour les tests
        qui portent sur le deroulement et non sur le tirage."""
        cfg = self.lab.cfg

        def juge(pf, palier):
            self.juges.append(palier.rang)
            if arret_impose is not None:
                return mod_prog.Jugement(palier.rang, arret_impose, ["arret impose par le test"])
            return mod_prog.juger_palier(pf, palier, self.paliers, cfg,
                                         confirmation=self.confirmation)

        self.juges = []
        with self.lab.registre() as reg:
            return mod_orch.evaluer_etape_progressive(
                reg, self.lab.moteur, self.lab.gen / "build", self.builds,
                [p.ident for p in self.pols], self.pols, ia_c, sha_c, self.ia_h, self.sha_h,
                self.blocs, 4242, self.lab.runs / travail, workers=1, taille_lot=4,
                paliers=self.paliers, juge=juge)


def _matchs(lab: Laboratoire, where: str = "1=1") -> int:
    with lab.registre() as reg:
        return reg.executer("SELECT COUNT(*) AS n FROM matchs WHERE %s" % where).fetchone()["n"]


def _comps(format_nom: str, moyennes: list[float], n: int, ecart: float) -> list[st.Composante]:
    """Composantes d'un format : n blocs par adversaire, moyenne et dispersion imposees."""
    out = []
    for i, m in enumerate(moyennes):
        d = [m + (ecart if k % 2 else -ecart) for k in range(n)]
        out.append(st.composante(format_nom, "adv%02d" % i, d))
    return out


def _cfg():
    return {"objectif": json.loads(json.dumps(OBJECTIF)), "crible": json.loads(json.dumps(CRIBLE))}


# --------------------------------------------------------------------------------------
# Regles d'arret, sur composantes construites
# --------------------------------------------------------------------------------------
def test_quantile_de_student():
    for alpha, nu, attendu in ((0.05, 1, 6.3138), (0.05, 10, 1.8125), (0.025, 5, 2.5706),
                               (0.10, 30, 1.3104), (0.05, 1e6, 1.6449)):
        q = mod_prog.quantile_t(alpha, nu)
        assert abs(q - attendu) < 2e-3, (alpha, nu, q, attendu)


def test_une_variance_empirique_nulle_ne_donne_pas_une_certitude():
    comps = _comps("farmer", [0.0, 0.0, 0.0], n=3, ecart=0.0)
    assert all(c.variance == 0.0 for c in comps)
    b = mod_prog.borne_haute_format(comps, 0.0825, 4, 0.10)
    assert b["borne_haute"] is not None and b["borne_haute"] > 0.05, b


def test_une_borne_solo_sous_zero_ne_suffit_pas_a_rejeter():
    """Le cas du cahier des charges : une baisse solo reelle mais au-dessus du plancher."""
    paliers = mod_prog.paliers_de({"blocs": {"solo": 120, "farmer": 120},
                                   "paliers": [{"solo": 120}, {"solo": 120, "farmer": 120}]})
    leger = {"solo": _comps("solo", [-0.04, -0.04, -0.04], n=40, ecart=0.19)}
    j = mod_prog.juger_palier(leger, paliers[0], paliers, _cfg())
    b = j.tests["plancher_solo"]
    assert b["borne_haute"] < 0.0, "le cas doit avoir une borne solo NEGATIVE : %r" % b
    assert b["borne_haute"] > -0.05, b
    assert j.arret is None, j.raisons

    lourd = {"solo": _comps("solo", [-0.12, -0.12, -0.12], n=40, ecart=0.19)}
    j = mod_prog.juger_palier(lourd, paliers[0], paliers, _cfg())
    assert j.arret == mod_prog.REJET_STATISTIQUE, j.tests


def test_la_futilite_eleveur_et_le_seuil_du_premier_palier():
    paliers = mod_prog.paliers_de({"blocs": {"farmer": 60},
                                   "paliers": [{"farmer": 6}, {"farmer": 30}, {"farmer": 60}]})
    # Au premier palier, le seuil tolerant (-0,05) laisse passer un candidat un peu en dessous
    # du gain minimal ; au palier suivant, le gain minimal (+0,01) s'applique.
    tiede = {"farmer": _comps("farmer", [-0.02, -0.02, -0.02], n=10, ecart=0.25)}
    j0 = mod_prog.juger_palier({"farmer": tiede["farmer"]}, paliers[0], paliers, _cfg())
    assert j0.tests["futilite_farmer"]["seuil"] == -0.05
    j1 = mod_prog.juger_palier(tiede, paliers[1], paliers, _cfg())
    assert j1.tests["futilite_farmer"]["seuil"] == 0.01
    # En confirmation, jamais de seuil tolerant.
    jc = mod_prog.juger_palier(tiede, paliers[0], paliers, _cfg(), confirmation=True)
    assert jc.tests["futilite_farmer"]["seuil"] == 0.01

    desastre = {"farmer": _comps("farmer", [-0.6, -0.5, -0.7], n=2, ecart=0.2)}
    j = mod_prog.juger_palier(desastre, paliers[0], paliers, _cfg())
    assert j.arret == mod_prog.ABANDON_CRIBLE, j.tests


def test_l_objectif_est_differe_tant_qu_un_format_pondere_manque():
    paliers = mod_prog.paliers_de({"blocs": {"farmer": 9, "solo": 9, "team": 9},
                                   "paliers": [{"farmer": 9, "solo": 9},
                                               {"farmer": 9, "solo": 9, "team": 9}]})
    pf = {"farmer": _comps("farmer", [0.02, 0.02, 0.02], n=3, ecart=0.2),
          "solo": _comps("solo", [0.0, 0.0, 0.0], n=3, ecart=0.2)}
    j = mod_prog.juger_palier(pf, paliers[0], paliers, _cfg())
    assert "differe" in j.tests["objectif"], j.tests["objectif"]
    pf["team"] = _comps("team", [0.0, 0.0, 0.0], n=3, ecart=0.2)
    j = mod_prog.juger_palier(pf, paliers[1], paliers, _cfg())
    assert j.tests["objectif"].get("borne_haute") is not None, j.tests["objectif"]


def test_le_juge_n_emet_jamais_de_decision_favorable():
    """Aucune promotion anticipee : le juge arrete ou laisse continuer, rien d'autre."""
    rnd = random.Random(7)
    paliers = mod_prog.paliers_de({"blocs": {"farmer": 12, "solo": 6, "team": 6},
                                   "paliers": [{"solo": 3}, {"solo": 3, "farmer": 6},
                                               {"farmer": 12, "solo": 6, "team": 6}]})
    for _ in range(300):
        pf = {}
        for f in ("farmer", "solo", "team"):
            if rnd.random() < 0.8:
                pf[f] = _comps(f, [rnd.uniform(-1, 1) for _ in range(3)],
                               n=rnd.randint(1, 5), ecart=rnd.uniform(0, 0.5))
        for p in paliers:
            for confirmation in (False, True):
                j = mod_prog.juger_palier(pf, p, paliers, _cfg(), confirmation=confirmation)
                assert j.arret in (None,) + mod_prog.ARRETS_DEFINITIFS, j.arret
    excellent = {f: _comps(f, [0.9, 0.9, 0.9], n=6, ecart=0.05) for f in ("farmer", "solo", "team")}
    for p in paliers:
        assert mod_prog.juger_palier(excellent, p, paliers, _cfg(), confirmation=True).arret is None


def test_des_paliers_incoherents_sont_refuses():
    for mauvais in ([{"farmer": 6}],                       # le dernier palier n'est pas l'etape
                    [{"farmer": 8}, {"farmer": 6}, {"farmer": 12}],   # decroissant
                    [{"farmer": 14}, {"farmer": 12}],     # au-dela de l'etape
                    [{"team": 2}, {"farmer": 12}]):       # format absent de l'etape
        try:
            mod_prog.paliers_de({"blocs": {"farmer": 12}, "paliers": mauvais})
        except ValueError:
            continue
        raise AssertionError("paliers acceptes a tort : %r" % mauvais)
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t)) as lab:
            cfg = json.loads(json.dumps(lab.cfg))
            cfg["etapes"]["s1"]["paliers"] = [{"solo": 2}, {"farmer": 3, "solo": 3}]
            panel = mod_ligue.charger(cfg["ligue"]["source"], None)
            problemes = cli.controler_faisabilite(cfg, panel, mod_sc.charger_builds())
            # s1 n'a qu'un adversaire dans la configuration du laboratoire : 2 blocs suffisent.
            assert problemes == [], problemes
            cfg["etapes"]["s2"]["paliers"] = [{"solo": 1, "farmer": 2}, {"farmer": 6, "solo": 6,
                                                                         "team": 2}]
            problemes = cli.controler_faisabilite(cfg, panel, mod_sc.charger_builds())
            assert any("s2 / palier 0 / solo" in p for p in problemes), problemes


# --------------------------------------------------------------------------------------
# Deroulement sur le moteur synthetique
# --------------------------------------------------------------------------------------
def test_un_candidat_desastreux_est_rejete_sans_lancer_la_suite():
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t), force_champion=FORCE_CHAMPION, crible=CRIBLE) as lab:
            lab.init_campagne()
            etape = Etape(lab)
            ia_c, sha_c = _bundle(lab, "desastre", -100)
            par_format, couverture, suivi = etape.jouer(ia_c, sha_c)

            assert suivi["arret"] == mod_prog.REJET_STATISTIQUE, suivi
            assert suivi["palier_arret"] == 0 and etape.juges == [0], (suivi, etape.juges)
            # Le controle solo a suffi : l'eleveur n'a jamais ete soumis.
            assert lab.synth.joues == 12, lab.synth.joues
            assert suivi["combats_prevus"] == PREVUS
            assert suivi["combats_evites"] == PREVUS - 12, suivi
            assert _matchs(lab, "format='farmer'") == 0
            assert "farmer" not in par_format and "farmer" not in couverture


def test_un_eleveur_futile_est_abandonne_au_premier_palier():
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t), force_champion=FORCE_CHAMPION, crible=CRIBLE) as lab:
            lab.init_campagne()
            etape = Etape(lab, blocs={"farmer": 12}, paliers=[{"farmer": 3}, {"farmer": 12}])
            ia_c, sha_c = _bundle(lab, "futile", -100)
            _pf, _cv, suivi = etape.jouer(ia_c, sha_c)
            assert suivi["arret"] == mod_prog.ABANDON_CRIBLE, suivi
            assert lab.synth.joues == 12 and suivi["combats_evites"] == 36, suivi


def test_une_petite_baisse_solo_laisse_mesurer_l_eleveur():
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t), force_champion=0, force_solo_champion=FORCE_CHAMPION,
                         crible=CRIBLE) as lab:
            lab.init_campagne()
            etape = Etape(lab)
            # Un peu moins bon que le champion en solo, nettement meilleur en eleveur.
            ia_c, sha_c = _bundle(lab, "baisse-solo", FORCE_CHAMPION,
                                  force_solo=FORCE_CHAMPION - 2)
            _pf, couverture, suivi = etape.jouer(ia_c, sha_c)
            assert suivi["arret"] is None, suivi
            assert etape.juges == [0, 1], etape.juges
            assert couverture["farmer"]["complet"] and couverture["farmer"]["blocs_complets"] == 6
            assert lab.synth.joues == PREVUS and suivi["combats_evites"] == 0, suivi


def test_un_bon_candidat_poursuit_jusqu_au_bout():
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t), crible=CRIBLE) as lab:
            lab.init_campagne()
            etape = Etape(lab)
            ia_c, sha_c = _bundle(lab, "bon", FORCE_CHAMPION)
            _pf, couverture, suivi = etape.jouer(ia_c, sha_c)
            assert suivi["arret"] is None, suivi
            # Le dernier palier n'est jamais juge ici : il revient au decideur complet.
            assert etape.juges == [0, 1], etape.juges
            assert [p["rang"] for p in suivi["paliers"]] == [0, 1, 2]
            assert suivi["combats_evites"] == 0
            assert all(c["complet"] for c in couverture.values())


def test_un_indecis_poursuit_a_chaque_palier_intermediaire():
    """Aucun ecart mesure, dispersion typique des campagnes : rien ne doit l'arreter, aux
    effectifs des paliers de la campagne v3."""
    paliers = mod_prog.paliers_de({"blocs": {"solo": 3, "farmer": 12},
                                   "paliers": [{"solo": 3}, {"solo": 3, "farmer": 6},
                                               {"solo": 3, "farmer": 12}]})
    ecart_solo, ecart_farmer = 0.0352 ** 0.5, 0.0825 ** 0.5
    pf0 = {"solo": _comps("solo", [0.0, 0.0, 0.0], n=1, ecart=ecart_solo)}
    j0 = mod_prog.juger_palier(pf0, paliers[0], paliers, _cfg())
    assert j0.arret is None, j0.tests
    pf1 = dict(pf0, farmer=_comps("farmer", [0.0, 0.0, 0.0], n=2, ecart=ecart_farmer))
    j1 = mod_prog.juger_palier(pf1, paliers[1], paliers, _cfg())
    assert j1.arret is None, j1.tests


def test_une_interruption_se_reprend_sans_doublon_et_rend_le_meme_resultat():
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t), crible=CRIBLE) as lab:
            lab.init_campagne()
            etape = Etape(lab)
            ia_c, sha_c = _bundle(lab, "bon", FORCE_CHAMPION)
            reference, _cv, _s = etape.jouer(ia_c, sha_c)

    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t), crible=CRIBLE) as lab:
            lab.init_campagne()
            etape = Etape(lab)
            ia_c, sha_c = _bundle(lab, "bon", FORCE_CHAMPION)
            # Coupure au milieu du palier 1 : le palier 0 (12 combats) est complet.
            lab.synth.plafond = 16
            _pf, _cv, coupe = etape.jouer(ia_c, sha_c)
            assert coupe["arret"] == mod_prog.INTERROMPU and coupe["palier_arret"] == 1, coupe
            assert etape.juges == [0], "un palier incomplet ne se juge pas : %r" % etape.juges
            assert "tests" not in coupe["paliers"][-1]
            # Le palier 2 n'a jamais ete prepare.
            assert coupe["combats_evites"] == PREVUS - 4 * 6, coupe
            assert _matchs(lab, "erreur='aucune'") == 16

            lab.synth.plafond = None
            lab.synth.joues = 0
            repris, _cv, suite = etape.jouer(ia_c, sha_c)
            assert suite["arret"] is None, suite
            # Seuls les combats manquants sont joues ; ceux deja valides viennent du cache.
            assert lab.synth.joues == PREVUS - 16, lab.synth.joues
            assert suite["combats_du_cache"] == 16, suite
            assert _matchs(lab, "erreur='aucune'") == PREVUS
            assert repris == reference, "la reprise doit rendre le resultat du parcours continu"


def test_le_cache_de_reference_est_partage_entre_candidats():
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t), crible=CRIBLE) as lab:
            lab.init_campagne()
            etape = Etape(lab, blocs={"farmer": 12}, paliers=[{"farmer": 3}, {"farmer": 12}])

            ia_a, sha_a = _bundle(lab, "a-arrete", -100)
            _pf, _cv, a = etape.jouer(ia_a, sha_a, travail="a",
                                      arret_impose=mod_prog.ABANDON_CRIBLE)
            assert a["arret"] == mod_prog.ABANDON_CRIBLE and a["combats_joues"] == 12, a

            # B reprend les 6 combats de reference du palier 0 joues pour A ; ceux du palier 1
            # n'existaient pas encore.
            ia_b, sha_b = _bundle(lab, "b-bon", FORCE_CHAMPION)
            _pf, _cv, b = etape.jouer(ia_b, sha_b, travail="b")
            assert b["arret"] is None, b
            assert b["combats_du_cache"] == 6 and b["combats_joues"] == 42, b

            # C trouve toute la reference en cache : seuls ses propres combats sont joues.
            ia_c, sha_c = _bundle(lab, "c-bon", 30)
            _pf, _cv, c = etape.jouer(ia_c, sha_c, travail="c")
            assert c["arret"] is None, c
            assert c["combats_du_cache"] == 24 and c["combats_joues"] == 24, c


def test_des_blocs_incomplets_interrompent_sans_juger():
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t), force_champion=FORCE_CHAMPION, crible=CRIBLE) as lab:
            lab.init_campagne()
            etape = Etape(lab)
            lab.synth.panne_contre = etape.pols[1].sha256[:16]
            ia_c, sha_c = _bundle(lab, "desastre", -100)
            _pf, couverture, suivi = etape.jouer(ia_c, sha_c)
            # Meme un candidat desastreux n'est pas juge sur un palier incomplet.
            assert suivi["arret"] == mod_prog.INTERROMPU and suivi["palier_arret"] == 0, suivi
            assert etape.juges == [], etape.juges
            assert not couverture["solo"]["complet"]
            assert lab.synth.joues == 8, lab.synth.joues
            assert suivi["combats_evites"] == PREVUS - 12, suivi


def test_la_configuration_v3_est_faisable_et_porte_le_crible_retenu():
    """La configuration livree pour la campagne v3, pas une autre."""
    cfg = cli.charger_config(RACINE / "config" / "v3.yaml")
    try:
        assert {k: cfg["crible"][k] for k in CRIBLE} == CRIBLE, cfg["crible"]
        assert cfg["objectif"] == OBJECTIF
        # Panel de la meme taille que la ligue v3 : dix versions, puis l'ancre.
        panel = [types.SimpleNamespace(ident="v%02d" % i) for i in range(10)]
        panel.append(types.SimpleNamespace(ident="ancre"))
        problemes = cli.controler_faisabilite(cfg, panel, mod_sc.charger_builds())
        assert problemes == [], problemes
        for nom, spec in cfg["etapes"].items():
            paliers = mod_prog.paliers_de(spec)
            assert len(paliers) >= 2, "%s : aucune possibilite d'arret anticipe" % nom
            assert spec.get("adversaires", 11) <= 10, "l'ancre ne doit jamais decider (%s)" % nom
        s1 = mod_prog.paliers_de(cfg["etapes"]["s1"])
        assert list(s1[0].blocs) == ["solo"], "S1 commence par un controle solo minuscule"
    finally:
        cli.appliquer_profil({})


# --------------------------------------------------------------------------------------
# Boucle complete
# --------------------------------------------------------------------------------------
ETAPES_BOUCLE = {
    "s1": {"candidats": 2, "blocs": {"farmer": 6, "solo": 3}, "adversaires": 3, "garder": 2,
           "paliers": [{"solo": 3}, {"solo": 3, "farmer": 3}, {"solo": 3, "farmer": 6}]},
    "s2": {"blocs": {"farmer": 9, "solo": 6, "team": 3}, "adversaires": 3, "garder": 1,
           "paliers": [{"farmer": 6, "solo": 3}, {"farmer": 9, "solo": 6, "team": 3}]},
    "s3": {"blocs": {"farmer": 12, "solo": 6, "team": 3}, "adversaires": 3, "garder": 1},
    "confirmation": {"blocs": {"farmer": 24, "solo": 9, "team": 9}, "adversaires": 3,
                     "graines_neuves": True, "formats_requis": ["farmer", "solo", "team"],
                     "paliers": [{"solo": 3}, {"solo": 3, "farmer": 9},
                                 {"farmer": 24, "solo": 9, "team": 9}]},
}


def test_la_boucle_n_ouvre_pas_l_etape_suivante_apres_un_arret():
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t), etapes=ETAPES_BOUCLE, crible=CRIBLE) as lab:
            assert lab.init_campagne() == 0
            patches = lab.ecrire_patches({"a-desastre": -100, "b-bon": FORCE_CHAMPION})
            lab.run_loop(patches, candidats=2, confirmations=0, minutes=10.0)
            rapport = lab.rapport_boucle()
            s1 = {l["candidat"]: l for l in rapport["journal"] if l.get("etape") == "s1"}
            desastre = s1["essai-a-desastre"]
            assert desastre["decision"] in mod_prog.ARRETS_DEFINITIFS, desastre
            assert desastre["crible"]["combats_evites"] > 0, desastre
            assert not any(l.get("candidat") == "essai-a-desastre" and l.get("etape") != "s1"
                           for l in rapport["journal"] if l.get("etape")), rapport["journal"]
            assert any(l.get("candidat") == "essai-b-bon" and l.get("etape") == "s2"
                       for l in rapport["journal"]), rapport["journal"]
            # Le rapport d'etape dit ce qui a ete evite, et que l'arret ne prouve rien de general.
            r = json.loads((lab.runs / "rapports" / "essai-a-desastre-s1.json")
                           .read_text(encoding="utf-8"))
            assert r["crible"]["arret"] == desastre["decision"]
            assert any("ne prouve pas" in x for x in r["decision"]["raisons"]), r["decision"]

            # Relance : l'arret est repris tel quel, sans combat et sans ouvrir S2.
            lab.synth.joues = 0
            lab.run_loop(patches, candidats=2, confirmations=0, minutes=10.0)
            second = lab.rapport_boucle()
            assert not any(l.get("candidat") == "essai-a-desastre" and l.get("etape") == "s2"
                           for l in second["journal"]), second["journal"]
            assert lab.synth.joues == 0, lab.synth.joues


def test_une_confirmation_progressive_ne_promeut_qu_apres_son_plan_complet():
    with tempfile.TemporaryDirectory() as t:
        with Laboratoire(Path(t), etapes=ETAPES_BOUCLE, crible=CRIBLE) as lab:
            assert lab.init_campagne() == 0
            patches = lab.ecrire_patches({"c-fort": 45})
            lab.run_loop(patches, candidats=1, confirmations=0, minutes=30.0)

            # Coupure au milieu du palier 1 de la confirmation : le palier 0 est complet et
            # tres favorable, mais rien n'est promu.
            lab.synth.joues = 0
            lab.synth.plafond = 12 + 20
            lab.run_loop(patches, candidats=1, confirmations=1, minutes=30.0)
            coupe = lab.rapport_boucle()
            conf = [l for l in coupe["journal"] if l.get("etape") == "confirmation"][0]
            assert conf["decision"] == mod_prog.INTERROMPU, conf
            assert coupe["promotions"] == 0
            with lab.registre() as reg:
                assert reg.champion_courant()["champion_courant"] == "champion-000"
                ligne = reg.confirmation_reprenable("essai", "essai-c-fort")
                assert ligne is not None and ligne["etat"] == "partielle", dict(ligne or {})
                assert reg.confirmations_utilisees("essai") == 1

            # Reprise : meme tentative, seuls les combats manquants, decision du protocole
            # complet.
            partiels = lab.synth.joues
            lab.synth.plafond = None
            lab.synth.joues = 0
            lab.run_loop(patches, candidats=1, confirmations=1, minutes=30.0)
            fini = lab.rapport_boucle()
            conf = [l for l in fini["journal"] if l.get("etape") == "confirmation"][0]
            assert conf["reprise"] is True, conf
            prevus = 4 * sum(ETAPES_BOUCLE["confirmation"]["blocs"].values())
            assert partiels == 32 and partiels + lab.synth.joues == prevus, \
                (partiels, lab.synth.joues, prevus)
            assert conf["decision"] == "PROMOUVOIR", conf
            assert fini["promotions"] == 1
            r = json.loads((lab.runs / "rapports" / "essai-c-fort-confirmation-01.json")
                           .read_text(encoding="utf-8"))
            assert all(c["complet"] for c in r["couverture"].values()), r["couverture"]
            assert r["crible"]["arret"] is None
            with lab.registre() as reg:
                assert reg.champion_courant()["champion_courant"] == "champion-001"
                assert reg.confirmations_utilisees("essai") == 1


if __name__ == "__main__":
    echecs = 0
    for nom, fn in sorted(globals().items()):
        if not nom.startswith("test_") or not callable(fn):
            continue
        t0 = time.monotonic()
        try:
            fn()
            print("  ok   %-66s %5.1f s" % (nom, time.monotonic() - t0))
        except Exception as e:
            echecs += 1
            import traceback
            print("  ECHEC %s : %s" % (nom, e))
            traceback.print_exc()
    print("\n%s" % ("tous les tests du crible passent" if not echecs else "%d echec(s)" % echecs))
    raise SystemExit(1 if echecs else 0)

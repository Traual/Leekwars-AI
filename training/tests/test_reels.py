"""Tests de reception qui JOUENT de vrais combats.

Separes du reste parce qu'ils coutent des minutes, pas des millisecondes. Ils repondent aux
questions que seul le moteur peut trancher : deux workers donnent-ils le meme resultat qu'un
seul, un combat dont les IA n'ont pas tourne est-il reconnu comme tel, et le format team
construit-il vraiment deux camps de quatre poireaux.

    python training/tests/test_reels.py
"""
from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

import yaml                          # noqa: E402
import bundle as mod_bundle          # noqa: E402
import evaluator as mod_eval         # noqa: E402
import scenarios as mod_sc           # noqa: E402
import registry as mod_reg           # noqa: E402

RUNS = RACINE / "runs"


def _contexte():
    cfg = yaml.safe_load((RACINE / "config" / "loop.yaml").read_text(encoding="utf-8"))
    moteur = mod_eval.Moteur.detecter(cfg["moteur"]["racine"], cfg["moteur"]["java_home"])
    build = mod_eval.compiler_runner(moteur, RUNS / ".build")
    return cfg, moteur, build


def _ia_champion(moteur):
    with mod_reg.Registre() as reg:
        courant = reg.champion_courant()
    emp = mod_bundle.empreinte(courant["commit_code"])
    court = emp["sha256"][:16]
    mod_bundle.materialiser(courant["commit_code"],
                            moteur.racine / "test" / "ai" / "bundles" / court)
    return "test/ai/bundles/%s/Main.leek" % court


def test_deux_workers_donnent_les_memes_resultats():
    """Un worker = une JVM. Le parallelisme decoupe la FILE, pas le moteur.

    Le moteur porte des etats statiques ; rien ne prouve leur isolation entre threads. Ce test
    est la seule raison d'oser deux workers : sans lui, un gain de debit pourrait cacher des
    resultats subtilement differents, et toute la campagne serait bruitee par son propre banc.
    """
    _cfg, moteur, build = _contexte()
    builds = mod_sc.charger_builds()
    ia = _ia_champion(moteur)
    travail = RUNS / "verif-workers"
    travail.mkdir(parents=True, exist_ok=True)

    chemins = []
    for f, n in (("solo", 4), ("farmer", 4)):
        for b in mod_sc.plan_de_blocs(f, ["verif"], n, 987654, builds):
            p = travail / ("%s_%d.json" % (f, b.indice))
            mod_sc.ecrire(p, mod_sc.scenario(b, builds, ia, ia))
            chemins.append(p)

    t0 = time.monotonic()
    sequentiel = mod_eval.executer_lot(moteur, build, chemins)
    t_seq = time.monotonic() - t0

    from concurrent.futures import ThreadPoolExecutor
    lots = [chemins[0::2], chemins[1::2]]
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        sorties = list(pool.map(lambda l: mod_eval.executer_lot(moteur, build, l), lots))
    t_par = time.monotonic() - t0
    parallele = {}
    for lot, bruts in zip(lots, sorties):
        for c, brut in zip(lot, bruts):
            parallele[c.name] = brut

    ecarts = []
    for c, brut in zip(chemins, sequentiel):
        autre = parallele[c.name]
        for champ in ("winner", "duration"):
            if brut.get(champ) != autre.get(champ):
                ecarts.append((c.name, champ, brut.get(champ), autre.get(champ)))
    assert not ecarts, "deux workers ont donne d'autres resultats : %s" % ecarts
    print("   %d combats identiques | sequentiel %.1f s, deux workers %.1f s"
          % (len(chemins), t_seq, t_par))


def test_le_format_team_donne_deux_camps_de_quatre_dans_le_moteur():
    """Le controle porte sur l'etat MOTEUR, pas sur le JSON du scenario.

    Le generateur cree un camp par sous-liste de `entities` ; le champ JSON `team` ne sert
    qu'a nommer l'equipe. Une team ecrite en quatre sous-listes de deux poireaux donnait donc
    quatre camps, et les deux eleveurs censes cooperer se battaient entre eux. Le vainqueur
    binaire lu par l'evaluateur n'avait alors aucun sens.
    """
    _cfg, moteur, build = _contexte()
    builds = mod_sc.charger_builds()
    ia = _ia_champion(moteur)
    travail = RUNS / "verif-team"
    travail.mkdir(parents=True, exist_ok=True)

    bloc = mod_sc.plan_de_blocs("team", ["verif"], 1, 20260911, builds)[0]
    sc = mod_sc.scenario(bloc, builds, ia, ia)
    attendus = mod_sc.camps_attendus(sc)
    chemin = travail / "team.json"
    mod_sc.ecrire(chemin, sc)

    brut = mod_eval.executer_lot(moteur, build, [chemin])[0]
    score, err, detail = mod_eval.analyser(brut)
    # Des tours avortes sont du JEU (plafond d'operations) : le combat compte. Seules les
    # categories disqualifiantes invalideraient ce controle.
    assert err not in mod_eval.ERREURS_DISQUALIFIANTES, "combat de team invalide : %s" % detail
    assert score is not None

    par_camp: dict[int, list[int]] = {}
    for e in brut["entities"]:
        if e.get("summon"):
            continue
        par_camp.setdefault(e["team"], []).append(e["id"])
    assert len(par_camp) == 2, ("le moteur a construit %d camps : %s"
                                % (len(par_camp), {k: sorted(v) for k, v in par_camp.items()}))
    # Le moteur renumerote les entites dans l'ordre d'ajout : le camp k doit donc etre
    # exactement le k-ieme bloc contigu, de la taille de la k-ieme sous-liste du scenario.
    tailles = [len(g) for g in attendus]
    prochain = 0
    for taille, camp in zip(tailles, [par_camp[k] for k in sorted(par_camp)]):
        assert sorted(camp) == list(range(prochain, prochain + taille)), (par_camp, tailles)
        prochain += taille
    # Deux proprietaires par camp : la composition team est bien une team, pas un duel.
    proprietaires = Counter()
    for groupe in sc["entities"]:
        proprietaires[len({e["farmer"] for e in groupe})] += 1
    assert list(proprietaires) == [2], proprietaires
    print("   camps moteur : %s" % {k: sorted(v) for k, v in par_camp.items()})


def test_un_combat_sans_ia_est_reconnu_par_la_fonction_du_pilote():
    """Un bundle HORS de la racine du generateur se lance sans erreur, puis leve a chaque tour.

    Le `NativeFileSystem` du compilateur resout depuis sa propre racine et refuse tout ce qui
    en sort (`resolveSafe`). Un bundle place ailleurs — meme designe par un chemin absolu
    parfaitement valide pour le systeme — n'est pas trouve, et le combat se termine en une
    fraction de seconde parce qu'il n'a rien calcule. C'est exactement ce qui est arrive
    pendant la construction : 128 erreurs par combat et quatre dixiemes de seconde, pris pour
    un debit exceptionnel.

    Nuance apprise en ecrivant ce test : ce n'est PAS l'absolu qui casse. Un chemin absolu vers
    un bundle situe SOUS la racine fonctionne. C'est la sortie de racine qui casse.

    Et le controle teste ici est celui du PILOTE : `mod_eval.valide_pour_le_debit`, la fonction
    que `calibrate` appelle. Une version definie dans le test seul ne protegeait rien.
    """
    _cfg, moteur, build = _contexte()
    builds = mod_sc.charger_builds()
    ia_relatif = _ia_champion(moteur)
    # Copie du meme bundle hors de la racine du generateur.
    dehors = RUNS / "verif-hors-racine"
    if not dehors.exists():
        mod_bundle.copier(moteur.racine / ia_relatif.rsplit("/", 1)[0], dehors)
    ia_absolu = str((dehors / "Main.leek").resolve())

    travail = RUNS / "verif-chemin"
    travail.mkdir(parents=True, exist_ok=True)
    b = mod_sc.plan_de_blocs("solo", ["verif"], 1, 4242, builds)[0]

    bon = travail / "bon.json"
    mod_sc.ecrire(bon, mod_sc.scenario(b, builds, ia_relatif, ia_relatif))
    mauvais = travail / "absolu.json"
    mod_sc.ecrire(mauvais, mod_sc.scenario(b, builds, ia_absolu, ia_absolu))

    r_bon, r_mauvais = mod_eval.executer_lot(moteur, build, [bon, mauvais])

    _s, err_bon, detail = mod_eval.analyser(r_bon)
    assert err_bon == mod_eval.ERREUR_AUCUNE, "le combat de reference doit etre propre : %s" % detail

    erreurs_ia = len(r_mauvais.get("ai_errors") or [])
    assert erreurs_ia > 0, ("le chemin hors racine aurait du faire lever l'IA a chaque tour ; "
                            "s'il passe, ce test ne protege plus rien")
    # Le moteur le DIT : une erreur systeme de chargement, pas un simple tour avorte.
    chargement = mod_eval.erreurs_de_chargement(r_mauvais)
    assert chargement, ("le runner doit remonter l'erreur systeme de chargement ; sans elle "
                        "rien ne distingue ce combat d'un depassement d'operations")
    assert mod_eval.classer(r_mauvais)[0] == mod_eval.ERREUR_CHARGEMENT

    bon_ok, _raison = mod_eval.valide_pour_le_debit(r_bon)
    mauvais_ok, raison = mod_eval.valide_pour_le_debit(r_mauvais)
    assert bon_ok, "le combat sain doit nourrir la mesure de debit"
    assert not mauvais_ok, ("un combat ou les IA n'ont pas ete chargees ne doit jamais "
                            "alimenter une mesure de debit")
    print("   combat sain : %d erreurs | combat hors racine : %d erreurs, %s"
          % (len(r_bon.get("ai_errors") or []), erreurs_ia, raison))


if __name__ == "__main__":
    echecs = 0
    for nom, fn in sorted(globals().items()):
        if not nom.startswith("test_") or not callable(fn):
            continue
        print(" ", nom)
        try:
            fn()
            print("   ok")
        except Exception as e:
            echecs += 1
            print("   ECHEC :", e)
    print("\n%s" % ("tous les tests reels passent" if not echecs else "%d echec(s)" % echecs))
    raise SystemExit(1 if echecs else 0)

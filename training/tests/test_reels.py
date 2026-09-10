"""Tests de reception qui JOUENT de vrais combats.

Separes du reste parce qu'ils coutent des minutes, pas des millisecondes. Ils repondent aux
questions que seul un combat peut trancher : deux workers donnent-ils le meme resultat qu'un
seul, et un combat dont les IA n'ont pas tourne peut-il polluer une mesure.

    python training/tests/test_reels.py
"""
from __future__ import annotations

import json
import sys
import time
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


def test_un_combat_sans_ia_ne_compte_pas():
    """Un bundle HORS de la racine du generateur se lance sans erreur, puis leve a chaque tour.

    Le `NativeFileSystem` du compilateur resout depuis sa propre racine. Un bundle place
    ailleurs — meme designe par un chemin absolu parfaitement valide pour le systeme — n'est
    pas trouve, et le combat se termine en une fraction de seconde parce qu'il n'a rien
    calcule. C'est exactement ce qui est arrive pendant la construction : 128 erreurs par
    combat et quatre dixiemes de seconde, pris pour un debit exceptionnel.

    Nuance apprise en ecrivant ce test : ce n'est PAS l'absolu qui casse. Un chemin absolu vers
    un bundle situe SOUS la racine fonctionne. C'est la sortie de racine qui casse.
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

    _s, err_bon, _d = mod_eval.analyser(r_bon)
    assert err_bon == mod_eval.ERREUR_AUCUNE, "le combat de reference doit etre propre"

    erreurs_ia = len(r_mauvais.get("ai_errors") or [])
    assert erreurs_ia > 0, ("le chemin absolu aurait du faire lever l'IA a chaque tour ; s'il "
                            "passe, ce test ne protege plus rien")

    # La regle : un combat dont les IA n'ont pas tourne ne nourrit aucune mesure de debit.
    def valide_pour_le_debit(brut):
        _sc, err, _dt = mod_eval.analyser(brut)
        if err in (mod_eval.ERREUR_COMPILATION, mod_eval.ERREUR_INFRA, mod_eval.ERREUR_MANQUANT):
            return False
        # Toutes les entites ont-elles echoue a chaque tour ? Alors rien n'a ete calcule.
        # Le compteur se lit sur CE combat : le lire sur un autre etait le bug de la premiere
        # version de ce test, qui rejetait aussi le combat sain.
        n_err = len(brut.get("ai_errors") or [])
        tours = brut.get("duration") or 0
        entites = len(brut.get("entities") or []) or 1
        return not (tours > 0 and n_err >= tours * entites * 0.5)

    assert valide_pour_le_debit(r_bon)
    assert not valide_pour_le_debit(r_mauvais), (
        "un combat ou les IA levent a chaque tour ne doit jamais alimenter une mesure de debit")
    print("   combat sain : %d erreurs | combat a chemin absolu : %d erreurs, ecarte"
          % (len(r_bon.get("ai_errors") or []), erreurs_ia))


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

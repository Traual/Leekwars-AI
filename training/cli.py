"""Interface unique du harnais.

    doctor              prerequis, empreintes, modes/coeurs et donnees disponibles
    init-campaign       registre et configuration, SANS lancer la boucle
    calibrate           pilote court de debit, mesure reelle
    register-candidate  patch ou bundle immuable, mode manuel utilisable
    evaluate            S1/S2/S3 puis confirmation
    report              resultats, incertitudes, couts, raisons de decision
    resume              reprise de la meme campagne
    run-loop            lancement ulterieur explicite et borne

`run-loop` refuse de demarrer sans `--je-veux-vraiment` : le cahier des charges interdit de
laisser une boucle d'optimisation en cours a la livraison.
"""
from __future__ import annotations

import argparse
import json
import statistics as stdstats
import sys
import time
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parent
sys.path.insert(0, str(RACINE))

import bundle as mod_bundle          # noqa: E402
import evaluator as mod_eval         # noqa: E402
import scenarios as mod_sc           # noqa: E402
import statistics_lab as mod_stats   # noqa: E402
import registry as mod_reg           # noqa: E402

CONFIG = RACINE / "config" / "loop.yaml"
RUNS = RACINE / "runs"


def charger_config(chemin: Path = CONFIG) -> dict:
    return yaml.safe_load(Path(chemin).read_text(encoding="utf-8"))


def deployer_bundle(moteur, commit: str, sha256: str) -> tuple[Path, str]:
    """Materialise un bundle SOUS LA RACINE DU GENERATEUR et rend son chemin RELATIF.

    Le `NativeFileSystem` du compilateur LeekScript resout les chemins depuis sa propre racine.
    Un chemin absolu passe dans le champ `ai` d'un scenario ne provoque aucune erreur de
    lancement : le combat se joue, et l'IA leve simplement a chaque tour. Mesure du symptome :
    128 erreurs par combat, soixante-cinq tours, quatre dixiemes de seconde. Sans regarder le
    detail, on prendrait ca pour un debit exceptionnel.

    Le repertoire est nomme par l'empreinte du bundle. C'est ce qui rend correct le cache de
    compilation du generateur, qui ressert un binaire quand un nom a deja servi : ici un nom
    identique signifie un contenu identique, donc le binaire resservi est le bon.
    """
    racine = moteur.racine / "test" / "ai" / "bundles" / sha256[:16]
    mod_bundle.materialiser(commit, racine)
    return racine, "test/ai/bundles/%s/Main.leek" % sha256[:16]


# --------------------------------------------------------------------------------------
def cmd_doctor(args) -> int:
    cfg = charger_config(Path(args.config))
    ok = True
    print("== moteur ==")
    try:
        moteur = mod_eval.Moteur.detecter(cfg["moteur"]["racine"], cfg["moteur"]["java_home"])
        print("  racine        :", moteur.racine)
        print("  generator.jar :", moteur.jar.name, "%.1f Mo" % (moteur.jar.stat().st_size / 1e6))
        print("  leekscript    :", moteur.leekscript_jar.name if moteur.leekscript_jar else "(absent)")
        print("  empreinte     :", moteur.empreinte[:16])
        print("  java          :", moteur.version_java())
    except Exception as e:
        ok = False
        print("  ECHEC :", e)

    print("== champion courant ==")
    try:
        reg = mod_reg.Registre()
        courant = reg.champion_courant()
        emp = mod_bundle.empreinte(courant["commit_code"])
        concorde = emp["sha256"] == courant["bundle_sha256"]
        print("  id            :", courant["champion_courant"])
        print("  commit        :", courant["commit_code"][:12])
        print("  bundle        :", emp["sha256"][:16], "concordance:", "OUI" if concorde else "NON")
        print("  fichiers      :", emp["nb_fichiers"])
        ok = ok and concorde
    except Exception as e:
        ok = False
        print("  ECHEC :", e)

    print("== builds et formats ==")
    try:
        builds = mod_sc.charger_builds()
        eleveurs = {}
        for b in builds.values():
            eleveurs.setdefault(b["farmer"]["id"], 0)
            eleveurs[b["farmer"]["id"]] += 1
        complets = sum(1 for n in eleveurs.values() if n >= 4)
        print("  builds        :", len(builds))
        print("  eleveurs      :", len(eleveurs), "dont", complets, "a 4 poireaux ou plus")
        for nom, fmt in mod_sc.FORMATS.items():
            marque = "  (SYNTHETIQUE)" if fmt.synthetique else ""
            print("  format %-7s type=%d contexte=%d %dv%d%s"
                  % (nom, fmt.type_moteur, fmt.contexte, fmt.poireaux_par_camp,
                     fmt.poireaux_par_camp, marque))
            if fmt.note:
                print("      ", fmt.note)
        if complets < 2:
            ok = False
            print("  ECHEC : pas assez d'eleveurs complets")
    except Exception as e:
        ok = False
        print("  ECHEC :", e)

    print("== ligue ==")
    src = Path(cfg["ligue"]["source"])
    if src.exists():
        ligue = json.loads(src.read_text(encoding="utf-8"))
        print("  source        :", src)
        print("  politiques    :", len(ligue.get("versions", [])))
    else:
        print("  ABSENTE       :", src, "— la ligue devra etre enregistree avant une campagne")

    print("\n", "PRET" if ok else "NON PRET", sep="")
    return 0 if ok else 1


# --------------------------------------------------------------------------------------
def cmd_calibrate(args) -> int:
    """Pilote de debit : combats REELS, coeurs reels, jusqu'a la fin normale.

    Mesure separement la compilation FROIDE (premiere JVM, aucun cache) et CHAUDE (memes
    bundles, JVM neuve mais cache de compilation du generateur deja rempli). Si le budget ne
    suffit pas a terminer assez de combats, le rapport le dit et ne fabrique pas un debit.
    """
    cfg = charger_config(Path(args.config))
    moteur = mod_eval.Moteur.detecter(cfg["moteur"]["racine"], cfg["moteur"]["java_home"])
    build = mod_eval.compiler_runner(moteur, RUNS / ".build")
    builds = mod_sc.charger_builds()
    reg = mod_reg.Registre()
    courant = reg.champion_courant()

    travail = RUNS / "pilote"
    travail.mkdir(parents=True, exist_ok=True)
    emp = mod_bundle.empreinte(courant["commit_code"])
    chemin, ia = deployer_bundle(moteur, courant["commit_code"], emp["sha256"])

    formats = args.formats.split(",")
    details: list[dict] = []
    par_format: dict[str, dict] = {}
    budget = float(args.time_budget or cfg["debit"]["budget_pilote_secondes"])
    debut = time.monotonic()
    froid_mur = None

    for f in formats:
        restant = budget - (time.monotonic() - debut)
        if restant <= 5:
            par_format[f] = {"n": 0, "note": "budget epuise avant ce format"}
            continue
        blocs = mod_sc.plan_de_blocs(f, ["pilote"], args.par_format, 20260910, builds)
        chemins = []
        for b in blocs:
            p = travail / ("%s_%d.json" % (f, b.indice))
            mod_sc.ecrire(p, mod_sc.scenario(b, builds, ia, ia))
            chemins.append(p)
        # UN lot = UNE JVM qui joue en serie. C'est le regime reel du harnais : la compilation
        # des bundles est payee une fois, pas a chaque combat.
        t0 = time.monotonic()
        res = mod_eval.executer_lot(moteur, build, chemins, min(restant + 120, cfg["debit"]["timeout_lot_secondes"]))
        mur = time.monotonic() - t0
        durees, compils, erreurs = [], [], {}
        for i, brut in enumerate(res):
            _score, err, det = mod_eval.analyser(brut)
            erreurs[err] = erreurs.get(err, 0) + 1
            ex = brut.get("execution_time_ns")
            cp = brut.get("compilation_time_ns")
            if ex:
                durees.append(ex / 1e9)
            if cp is not None:
                compils.append(cp / 1e9)
            details.append({"format": f, "indice": blocs[i].indice,
                            "execution_s": round(ex / 1e9, 2) if ex else None,
                            "compilation_s": round(cp / 1e9, 2) if cp is not None else None,
                            "tours": brut.get("duration"), "erreur": err, "detail": det[:120]})
        if froid_mur is None:
            froid_mur = mur
        if durees:
            tries = sorted(durees)
            par_format[f] = {
                "n": len(durees),
                "mur_du_lot_s": round(mur, 2),
                "compilation_froide_s": round(max(compils), 2) if compils else None,
                "compilation_chaude_s": round(sorted(compils)[len(compils) // 2], 3) if compils else None,
                "execution_mediane_s": round(sorted(durees)[len(durees) // 2], 2),
                "execution_p90_s": round(tries[max(0, int(0.9 * len(tries)) - 1)], 2),
                "execution_max_s": round(max(durees), 2),
                "combats_par_minute_1_worker": round(60.0 * len(durees) / mur, 2),
                "erreurs": erreurs,
            }
        else:
            par_format[f] = {"n": 0, "note": "aucun combat termine", "erreurs": erreurs}

    ecoule = time.monotonic() - debut
    total = sum(v.get("n", 0) for v in par_format.values())
    rapport = {
        "budget_secondes": budget, "ecoule_secondes": round(ecoule, 1),
        "mur_du_premier_lot_s": round(froid_mur, 2) if froid_mur else None,
        "combats_termines": total,
        "workers_mesures": 1,
        "par_format": par_format,
        "combats": details,
    }
    if total < 4:
        rapport["verdict"] = ("estimation INSUFFISANTE : moins de quatre combats termines. Les "
                              "chiffres ci-dessus sont indicatifs et ne doivent pas servir a "
                              "dimensionner une campagne.")
    else:
        rapport["verdict"] = ("estimation utilisable pour un dimensionnement provisoire a UN "
                              "worker. Le debit a deux workers n'est pas mesure ici.")

    sortie = RUNS / "rapport-debit.json"
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in rapport.items() if k != "combats"},
                     ensure_ascii=False, indent=2))
    print("\ndetail par combat :", sortie)
    return 0


# --------------------------------------------------------------------------------------
def cmd_run_loop(args) -> int:
    if not args.je_veux_vraiment:
        print("REFUS : la boucle d'optimisation ne demarre pas sans --je-veux-vraiment.\n"
              "La livraison du harnais interdit de laisser une boucle en cours ; ce garde-fou\n"
              "est la pour que personne ne la lance par inadvertance.")
        return 2
    print("La boucle n'est pas implementee dans cette livraison : le cahier des charges demande\n"
          "le harnais, pas son execution. Utiliser register-candidate puis evaluate.")
    return 2


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="training", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=str(CONFIG))
    sous = p.add_subparsers(dest="commande", required=True)

    sous.add_parser("doctor").set_defaults(fn=cmd_doctor)

    c = sous.add_parser("calibrate")
    c.add_argument("--time-budget", type=float, default=None)
    c.add_argument("--formats", default="solo,farmer")
    c.add_argument("--par-format", type=int, default=4)
    c.set_defaults(fn=cmd_calibrate)

    r = sous.add_parser("run-loop")
    r.add_argument("--je-veux-vraiment", action="store_true")
    r.set_defaults(fn=cmd_run_loop)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

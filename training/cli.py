"""Interface unique du harnais d'amelioration du scoring.

    doctor              prerequis, empreintes, modes/coeurs et donnees disponibles
    init-campaign       registre et manifeste de campagne, SANS lancer la boucle
    calibrate           pilote de debit, mesure reelle, 1 ou plusieurs workers
    register-candidate  patch ou bundle -> commit immuable sur scoring-candidates/<id>
    evaluate            etapes de crible (s1/s2/s3) puis confirmation
    report              resultats, incertitudes, couts, raisons de decision
    audit-br            remplacement focal en battle royale, rapport sans veto
    resume              reconcilie une publication interrompue
    run-loop            boucle bornee : trois budgets obligatoires
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics as stdstats
import sys
import time
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parent
sys.path.insert(0, str(RACINE))

import bundle as mod_bundle          # noqa: E402
import candidates as mod_cand        # noqa: E402
import evaluator as mod_eval         # noqa: E402
import league as mod_ligue           # noqa: E402
import optimizer as mod_opt          # noqa: E402
import orchestrator as mod_orch      # noqa: E402
import publication as mod_pub        # noqa: E402
import registry as mod_reg           # noqa: E402
import scenarios as mod_sc           # noqa: E402
import statistics_lab as mod_stats   # noqa: E402

CONFIG = RACINE / "config" / "loop.yaml"
RUNS = RACINE / "runs"


def charger_config(chemin: Path = CONFIG) -> dict:
    return yaml.safe_load(Path(chemin).read_text(encoding="utf-8"))


def _contexte(args):
    cfg = charger_config(Path(args.config))
    moteur = mod_eval.Moteur.detecter(cfg["moteur"]["racine"], cfg["moteur"]["java_home"])
    build = mod_eval.compiler_runner(moteur, RUNS / ".build")
    return cfg, moteur, build


def deployer_bundle(moteur, commit: str, sha256: str) -> tuple[Path, str]:
    """Materialise un bundle SOUS LA RACINE DU GENERATEUR et rend son chemin RELATIF.

    Le `NativeFileSystem` du compilateur LeekScript resout depuis sa propre racine. Un chemin
    absolu dans le champ `ai` d'un scenario ne provoque AUCUNE erreur de lancement : le combat
    se joue et l'IA leve a chaque tour. Symptome mesure : 128 erreurs par combat, 65 tours,
    quatre dixiemes de seconde — sans regarder le detail, on prend ca pour un debit
    exceptionnel. D'ou le test de reception qui refuse un combat sans IA dans une mesure.

    Le repertoire est nomme par l'empreinte : le generateur ressert un binaire compile quand un
    nom a deja servi, et ici un nom identique signifie un contenu identique.
    """
    racine = moteur.racine / "test" / "ai" / "bundles" / sha256[:16]
    mod_bundle.materialiser(commit, racine)
    return racine, "test/ai/bundles/%s/Main.leek" % sha256[:16]


def _politiques(cfg, n: int | None = None):
    pols = mod_ligue.charger(cfg["ligue"]["source"], cfg["ligue"].get("ancre"))
    return pols if n is None else pols[:n]


# --------------------------------------------------------------------------------------
def cmd_doctor(args) -> int:
    cfg = charger_config(Path(args.config))
    ok = True
    print("== moteur ==")
    try:
        moteur = mod_eval.Moteur.detecter(cfg["moteur"]["racine"], cfg["moteur"]["java_home"])
        print("  racine        :", moteur.racine)
        print("  generator.jar : %.1f Mo" % (moteur.jar.stat().st_size / 1e6))
        print("  empreinte     :", moteur.empreinte[:16])
        print("  java          :", moteur.version_java())
    except Exception as e:
        ok = False
        print("  ECHEC :", e)

    print("== champion courant ==")
    try:
        with mod_reg.Registre() as reg:
            courant = reg.champion_courant()
        emp = mod_bundle.empreinte(courant["commit_code"])
        concorde = emp["sha256"] == courant["bundle_sha256"]
        print("  id            :", courant["champion_courant"])
        print("  commit        :", courant["commit_code"][:12])
        print("  bundle        :", emp["sha256"][:16], "concordance:", "OUI" if concorde else "NON")
        ok = ok and concorde
    except Exception as e:
        ok = False
        print("  ECHEC :", e)

    print("== builds et formats ==")
    try:
        builds = mod_sc.charger_builds()
        eleveurs: dict[int, int] = {}
        for b in builds.values():
            eleveurs[b["farmer"]["id"]] = eleveurs.get(b["farmer"]["id"], 0) + 1
        complets = sum(1 for n in eleveurs.values() if n >= 4)
        print("  builds        :", len(builds), "| eleveurs :", len(eleveurs),
              "dont", complets, "a 4 poireaux ou plus")
        for nom, fmt in mod_sc.FORMATS.items():
            print("  %-7s type=%d contexte=%d%s"
                  % (nom, fmt.type_moteur, fmt.contexte,
                     "  SYNTHETIQUE" if fmt.synthetique else ""))
        ok = ok and complets >= 2
    except Exception as e:
        ok = False
        print("  ECHEC :", e)

    print("== ligue ==")
    try:
        pols = _politiques(cfg)
        print("  politiques    :", len(pols))
        dbl = mod_ligue.doublons(pols)
        if dbl:
            print("  DOUBLONS      :", dbl, "— autant d'adversaires identiques, pas de diversite")
        for p in pols:
            print("    %-24s %-11s %s" % (p.ident, p.origine, p.sha256[:16]))
    except Exception as e:
        ok = False
        print("  ECHEC :", e)

    print("\n", "PRET" if ok else "NON PRET", sep="")
    return 0 if ok else 1


# --------------------------------------------------------------------------------------
def cmd_init_campaign(args) -> int:
    cfg, moteur, _build = _contexte(args)
    pols = _politiques(cfg)
    with mod_reg.Registre() as reg:
        courant = reg.champion_courant()
        conf = json.dumps(cfg, sort_keys=True, ensure_ascii=False)
        manifeste = {
            "id": cfg["campagne"]["id"],
            "cree_le": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "statut": "preparee",
            "champion_initial": courant,
            "ligue": mod_ligue.resume(pols),
            "doublons_de_ligue": mod_ligue.doublons(pols),
            "moteur": {"racine": str(moteur.racine), "empreinte": moteur.empreinte,
                       "java": moteur.version_java(),
                       "runner_sha256": hashlib.sha256(
                           mod_eval.SOURCE_RUNNER.read_bytes()).hexdigest()},
            "politique_coeurs": "reels, aucun plancher ni plafond",
            "tours_max": mod_sc.TOURS_MAX,
            "objectif": cfg["objectif"],
            "etapes": cfg["etapes"],
            "budgets": {"confirmations_max": cfg["campagne"]["confirmations_max"],
                        "risque_nominal_campagne": cfg["campagne"]["risque_nominal_campagne"],
                        "alpha_par_confirmation": cfg["campagne"]["alpha_par_confirmation"]},
            "empreinte_config": hashlib.sha256(conf.encode("utf-8")).hexdigest(),
            "_note_risque": ("Le risque nominal vaut pour CE budget de confirmations. Enchainer "
                             "des campagnes ne conserve pas 5 % de risque cumule."),
            "_note_builds": ("Le snapshot donne des BUILDS, pas les IA des joueurs du haut de "
                             "classement. Les resultats se lisent « contre les politiques de la "
                             "ligue sur des builds representatifs »."),
        }
        chemin = RUNS / ("campagne-%s.json" % cfg["campagne"]["id"])
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps(manifeste, ensure_ascii=False, indent=2), encoding="utf-8")
        reg.cx.execute("INSERT OR REPLACE INTO campagnes (id, cree_le, config_json,"
                       " empreinte_config, statut) VALUES (?,?,?,?,?)",
                       (manifeste["id"], manifeste["cree_le"], conf,
                        manifeste["empreinte_config"], "preparee"))
    print("campagne preparee :", chemin)
    print("champion initial  :", courant["champion_courant"], courant["commit_code"][:12])
    print("ligue             :", len(pols), "politiques")
    print("\nAucune boucle lancee. Etape suivante : register-candidate puis evaluate.")
    return 0


# --------------------------------------------------------------------------------------
def cmd_calibrate(args) -> int:
    cfg, moteur, build = _contexte(args)
    builds = mod_sc.charger_builds()
    with mod_reg.Registre() as reg:
        courant = reg.champion_courant()
    emp = mod_bundle.empreinte(courant["commit_code"])
    _chemin, ia = deployer_bundle(moteur, courant["commit_code"], emp["sha256"])

    travail = RUNS / "pilote"
    travail.mkdir(parents=True, exist_ok=True)
    formats = args.formats.split(",")
    budget = float(args.time_budget or cfg["debit"]["budget_pilote_secondes"])
    debut = time.monotonic()
    par_format: dict[str, dict] = {}
    details: list[dict] = []

    for f in formats:
        if budget - (time.monotonic() - debut) <= 5:
            par_format[f] = {"n_valides": 0, "note": "budget epuise avant ce format"}
            continue
        blocs = mod_sc.plan_de_blocs(f, ["pilote"], args.par_format, 20260910, builds)
        chemins = []
        for b in blocs:
            p = travail / ("%s_%d.json" % (f, b.indice))
            mod_sc.ecrire(p, mod_sc.scenario(b, builds, ia, ia))
            chemins.append(p)
        lots = [chemins[i::args.workers] for i in range(args.workers)]
        lots = [l for l in lots if l]
        t0 = time.monotonic()
        if args.workers > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                sorties = list(pool.map(
                    lambda l: mod_eval.executer_lot(moteur, build, l,
                                                    cfg["debit"]["timeout_lot_secondes"]), lots))
        else:
            sorties = [mod_eval.executer_lot(moteur, build, lots[0],
                                             cfg["debit"]["timeout_lot_secondes"])]
        mur = time.monotonic() - t0

        durees, compils, erreurs, valides = [], [], {}, 0
        for lot, bruts in zip(lots, sorties):
            for chemin, brut in zip(lot, bruts):
                _s, err, det = mod_eval.analyser(brut)
                erreurs[err] = erreurs.get(err, 0) + 1
                # Un combat dont les IA n'ont pas tourne ne nourrit AUCUNE mesure de debit : il
                # est rapide precisement parce qu'il n'a rien calcule.
                if err in (mod_eval.ERREUR_COMPILATION, mod_eval.ERREUR_INFRA,
                           mod_eval.ERREUR_MANQUANT):
                    continue
                valides += 1
                if brut.get("execution_time_ns"):
                    durees.append(brut["execution_time_ns"] / 1e9)
                if brut.get("compilation_time_ns") is not None:
                    compils.append(brut["compilation_time_ns"] / 1e9)
                details.append({"format": f, "fichier": chemin.name,
                                "execution_s": round(brut["execution_time_ns"] / 1e9, 2)
                                if brut.get("execution_time_ns") else None,
                                "tours": brut.get("duration"), "erreur": err,
                                "tours_avortes": len(brut.get("ai_errors") or [])})
        if durees:
            tries = sorted(durees)
            par_format[f] = {
                "n_valides": valides, "mur_s": round(mur, 2),
                "compilation_max_s": round(max(compils), 2) if compils else None,
                "execution_mediane_s": round(stdstats.median(durees), 2),
                "execution_p90_s": round(tries[max(0, int(0.9 * len(tries)) - 1)], 2),
                "execution_max_s": round(max(durees), 2),
                "combats_par_minute": round(60.0 * valides / mur, 2),
                "erreurs": erreurs,
            }
        else:
            par_format[f] = {"n_valides": 0, "erreurs": erreurs,
                             "note": "aucun combat VALIDE : rien a mesurer"}

    rapport = {"workers": args.workers, "budget_secondes": budget,
               "ecoule_secondes": round(time.monotonic() - debut, 1),
               "par_format": par_format, "combats": details}
    total = sum(v.get("n_valides", 0) for v in par_format.values())
    rapport["verdict"] = ("estimation INSUFFISANTE : moins de quatre combats valides."
                          if total < 4 else
                          "estimation utilisable pour un dimensionnement provisoire.")
    sortie = RUNS / ("rapport-debit-w%d.json" % args.workers)
    sortie.write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in rapport.items() if k != "combats"},
                     ensure_ascii=False, indent=2))
    print("\ndetail :", sortie)
    return 0


# --------------------------------------------------------------------------------------
def cmd_register_candidate(args) -> int:
    cfg = charger_config(Path(args.config))
    with mod_reg.Registre() as reg:
        courant = reg.champion_courant()
        parent = args.parent or courant["commit_code"]
        info = mod_cand.enregistrer(
            args.id, parent,
            patch=Path(args.patch) if args.patch else None,
            bundle_dir=Path(args.bundle) if args.bundle else None,
            hypothese=args.hypothese,
            portee=cfg["optimiseur"]["portee"], hors_portee=cfg["optimiseur"]["hors_portee"],
            dependances=[d for d in (args.dependances or "").split(",") if d])
        deja = reg.bundle_deja_evalue(info["bundle_sha256"])
        if deja is not None and deja["id"] != args.id:
            print("ATTENTION : bundle identique au candidat %s. Le rejouer ne mesurerait rien de"
                  " neuf." % deja["id"])
        reg.enregistrer_candidat(info["id"], cfg["campagne"]["id"], info["commit"],
                                 info["parent"], info["bundle_sha256"], info["hypothese"],
                                 {"fichiers": info["fichiers_modifies"],
                                  "verdict": info["verdict_portee"],
                                  "hors_portee": info["hors_portee"],
                                  "dependances": info["dependances"]})
        chemin = RUNS / "candidats" / ("%s.json" % info["id"])
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    if info["verdict_portee"] != "DANS_PORTEE":
        print("\nEXTENSION A EXAMINER : %s sort du perimetre convenu. Mesurer un patch tronque "
              "donnerait un resultat qui ne correspond a aucune idee." % info["hors_portee"])
    return 0


# --------------------------------------------------------------------------------------
def _evaluer(cfg, moteur, build, reg, cand: dict, etape: str, workers: int,
             blocs_override: dict | None = None) -> dict:
    builds = mod_sc.charger_builds()
    courant = reg.champion_courant()
    emp_h = mod_bundle.empreinte(courant["commit_code"])
    deployer_bundle(moteur, courant["commit_code"], emp_h["sha256"])
    ia_h = "test/ai/bundles/%s/Main.leek" % emp_h["sha256"][:16]
    deployer_bundle(moteur, cand["commit"], cand["bundle_sha256"])
    ia_c = "test/ai/bundles/%s/Main.leek" % cand["bundle_sha256"][:16]

    spec = dict(cfg["etapes"][etape])
    blocs = blocs_override or spec["blocs"]
    pols = _politiques(cfg, spec.get("adversaires"))
    confirmation = etape == "confirmation"
    # Graines de confirmation NEUVES : tirees apres le gel du candidat, jamais utilisees pour le
    # choisir ou le retoucher avant la decision.
    decalage = 1_000_000 if confirmation else 0
    graine = int(hashlib.sha256(cfg["campagne"]["id"].encode()).hexdigest()[:8], 16)

    t0 = time.monotonic()
    par_format = mod_orch.evaluer_etape(
        reg, moteur, build, builds, pols, ia_c, cand["bundle_sha256"], ia_h, emp_h["sha256"],
        blocs, pols, graine, RUNS / "matchs" / etape, decalage=decalage, workers=workers)
    mur = time.monotonic() - t0

    alpha = cfg["campagne"]["alpha_par_confirmation"] if confirmation else 0.05
    dec = mod_stats.decider(par_format, cfg["objectif"]["poids"],
                            cfg["objectif"]["planchers_empiriques"], alpha,
                            cfg["objectif"]["gain_minimal_farmer"])
    resultats = {}
    for f, comps in par_format.items():
        d, _v, a = mod_stats.agreger_format(comps)
        b, nu = mod_stats.borne_basse(d, a, [c.n for c in comps], alpha)
        resultats[f] = {"blocs": sum(c.n for c in comps), "adversaires": len(comps),
                        "delta_moyen": round(d, 4),
                        "borne_basse": None if b is None else round(b, 4),
                        "ddl_welch": None if nu is None else round(nu, 1),
                        "par_adversaire": {c.adversaire: {"n": c.n, "moyenne": round(c.moyenne, 4)}
                                           for c in comps}}
    return {"candidat": cand, "etape": etape, "champion": courant["champion_courant"],
            "alpha": alpha, "secondes": round(mur, 1),
            "protocole": {"coeurs": "reels", "tours_max": mod_sc.TOURS_MAX,
                          "graines": "neuves" if confirmation else "developpement",
                          "blocs": blocs, "adversaires": [p.ident for p in pols],
                          "taille_figee_avant_resultats": True},
            "resultats": resultats,
            "decision": {"verdict": dec.verdict, "raisons": dec.raisons,
                         "objectif_pondere": round(dec.j, 4),
                         "borne_basse_objectif":
                             None if dec.borne_j is None else round(dec.borne_j, 4)}}


def cmd_evaluate(args) -> int:
    cfg, moteur, build = _contexte(args)
    promu = None
    with mod_reg.Registre() as reg:
        ligne = reg.candidat(args.id)
        if ligne is None:
            print("candidat inconnu :", args.id)
            return 2
        cand = {"id": ligne["id"], "commit": ligne["commit_code"], "parent": ligne["parent"],
                "bundle_sha256": ligne["bundle_sha256"], "hypothese": ligne["hypothese"],
                "branche": "%s/%s" % (mod_cand.PREFIXE_BRANCHE, ligne["id"])}
        etape = "confirmation" if args.stage == "confirm" else args.stage
        override = json.loads(args.blocs) if args.blocs else None
        rapport = _evaluer(cfg, moteur, build, reg, cand, etape, args.workers, override)
        chemin = RUNS / "rapports" / ("%s-%s.json" % (args.id, etape))
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
        if etape == "confirmation" and rapport["decision"]["verdict"] == "PROMOUVOIR" \
                and args.promouvoir:
            promu = mod_pub.publier(reg, cand, rapport["champion"], rapport["decision"],
                                    rapport["protocole"])
            rapport["promotion"] = promu
            chemin.write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in rapport.items() if k != "candidat"},
                     ensure_ascii=False, indent=2))
    print("\nrapport :", chemin)
    if promu:
        print("PROMU :", promu["champion"], promu["tag"])
    return 0


def cmd_report(args) -> int:
    chemins = sorted((RUNS / "rapports").glob("%s-*.json" % (args.id or "*")))
    if not chemins:
        print("aucun rapport")
        return 2
    for c in chemins:
        r = json.loads(c.read_text(encoding="utf-8"))
        if "decision" not in r:
            continue
        print("=" * 78)
        print("%s | etape %s | champion %s | alpha %s"
              % (r["candidat"]["id"], r["etape"], r["champion"], r["alpha"]))
        print("hypothese :", r["candidat"].get("hypothese") or "(aucune)")
        for f, v in r["resultats"].items():
            print("  %-7s blocs %4d  delta %+.4f  borne %s  ddl %s"
                  % (f, v["blocs"], v["delta_moyen"], v["borne_basse"], v["ddl_welch"]))
        d = r["decision"]
        print("  objectif pondere %+.4f  borne %s"
              % (d["objectif_pondere"], d["borne_basse_objectif"]))
        print("  VERDICT :", d["verdict"])
        for raison in d["raisons"]:
            print("     -", raison)
    return 0


# --------------------------------------------------------------------------------------
def cmd_audit_br(args) -> int:
    cfg, moteur, build = _contexte(args)
    builds = mod_sc.charger_builds()
    with mod_reg.Registre() as reg:
        courant = reg.champion_courant()
        emp_h = mod_bundle.empreinte(courant["commit_code"])
        deployer_bundle(moteur, courant["commit_code"], emp_h["sha256"])
        ia_h = "test/ai/bundles/%s/Main.leek" % emp_h["sha256"][:16]
        ligne = reg.candidat(args.id) if args.id else None
        if ligne is None:
            ia_c, sha_c = ia_h, emp_h["sha256"]
        else:
            deployer_bundle(moteur, ligne["commit_code"], ligne["bundle_sha256"])
            ia_c = "test/ai/bundles/%s/Main.leek" % ligne["bundle_sha256"][:16]
            sha_c = ligne["bundle_sha256"]
        pols = _politiques(cfg)
        rapport = mod_orch.auditer_br(reg, moteur, build, builds, pols, ia_c, sha_c,
                                      ia_h, emp_h["sha256"], RUNS / "matchs" / "br",
                                      lobbies=args.lobbies,
                                      creneaux=cfg["br"]["creneaux_focaux_par_lobby"],
                                      workers=args.workers)
    chemin = RUNS / "rapports" / ("br-%s.json" % (args.id or "champion"))
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in rapport.items() if k != "lignes"},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_resume(args) -> int:
    with mod_reg.Registre() as reg:
        etat = mod_pub.reconcilier(reg)
    print(json.dumps(etat, ensure_ascii=False, indent=2))
    return 0 if etat["etat"] in ("rien_a_reconcilier", "terminee_par_reprise") else 1


# --------------------------------------------------------------------------------------
def cmd_run_loop(args) -> int:
    """Boucle BORNEE. Les trois budgets sont obligatoires : aucun lancement recursif illimite.

    Elle enregistre et CRIBLE ; elle ne confirme ni ne promeut. La confirmation reste une
    commande explicite, parce qu'elle coute cher et qu'elle engage le registre des champions.
    """
    cfg, moteur, build = _contexte(args)
    fin = time.monotonic() + args.budget_minutes * 60
    source = Path(args.patches)
    patches = sorted(source.glob("*.patch")) if source.is_dir() else []
    if not patches:
        print("aucun patch dans %s : le mode manuel attend des fichiers .patch." % source)
        return 2

    faits, journal = 0, []
    with mod_reg.Registre() as reg:
        for p in patches:
            if faits >= args.max_candidats:
                journal.append({"patch": p.name, "etat": "non_traite",
                                "detail": "plafond de candidats atteint"})
                continue
            if time.monotonic() > fin:
                journal.append({"patch": p.name, "etat": "non_traite",
                                "detail": "budget de temps epuise"})
                continue
            ident = "%s-%s" % (cfg["campagne"]["id"], p.stem)
            try:
                info = mod_cand.enregistrer(ident, reg.champion_courant()["commit_code"],
                                            patch=p, hypothese=p.stem,
                                            portee=cfg["optimiseur"]["portee"],
                                            hors_portee=cfg["optimiseur"]["hors_portee"])
            except Exception as e:
                journal.append({"patch": p.name, "etat": "rejete", "detail": str(e)[:200]})
                continue
            reg.enregistrer_candidat(info["id"], cfg["campagne"]["id"], info["commit"],
                                     info["parent"], info["bundle_sha256"], info["hypothese"],
                                     {"fichiers": info["fichiers_modifies"],
                                      "verdict": info["verdict_portee"]})
            faits += 1
            r = _evaluer(cfg, moteur, build, reg, info, "s1", args.workers)
            journal.append({"candidat": ident, "etape": "s1",
                            "delta_farmer": r["resultats"].get("farmer", {}).get("delta_moyen"),
                            "verdict": r["decision"]["verdict"]})
    etat = {"propositions": len(patches), "candidats_enregistres": faits,
            "confirmations_lancees": 0, "promotions": 0,
            "budgets": {"max_candidats": args.max_candidats,
                        "max_confirmations": args.max_confirmations,
                        "budget_minutes": args.budget_minutes},
            "journal": journal,
            "_note": ("La boucle crible seulement. La confirmation et la promotion restent "
                      "explicites : evaluate --stage confirm --promouvoir.")}
    chemin = RUNS / "rapport-boucle.json"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(etat, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(etat, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="training", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=str(CONFIG))
    s = p.add_subparsers(dest="commande", required=True)

    s.add_parser("doctor").set_defaults(fn=cmd_doctor)
    s.add_parser("init-campaign").set_defaults(fn=cmd_init_campaign)

    c = s.add_parser("calibrate")
    c.add_argument("--time-budget", type=float, default=None)
    c.add_argument("--formats", default="solo,farmer")
    c.add_argument("--par-format", type=int, default=6)
    c.add_argument("--workers", type=int, default=1)
    c.set_defaults(fn=cmd_calibrate)

    r = s.add_parser("register-candidate")
    r.add_argument("--id", required=True)
    r.add_argument("--parent", default=None)
    r.add_argument("--patch", default=None)
    r.add_argument("--bundle", default=None)
    r.add_argument("--hypothese", default="")
    r.add_argument("--dependances", default="")
    r.set_defaults(fn=cmd_register_candidate)

    e = s.add_parser("evaluate")
    e.add_argument("--id", required=True)
    e.add_argument("--stage", default="s1", choices=["s1", "s2", "s3", "confirm"])
    e.add_argument("--workers", type=int, default=1)
    e.add_argument("--blocs", default=None, help='ex: {"farmer":2,"solo":1}')
    e.add_argument("--promouvoir", action="store_true")
    e.set_defaults(fn=cmd_evaluate)

    rp = s.add_parser("report")
    rp.add_argument("--id", default=None)
    rp.set_defaults(fn=cmd_report)

    b = s.add_parser("audit-br")
    b.add_argument("--id", default=None)
    b.add_argument("--lobbies", type=int, default=12)
    b.add_argument("--workers", type=int, default=1)
    b.set_defaults(fn=cmd_audit_br)

    s.add_parser("resume").set_defaults(fn=cmd_resume)

    l = s.add_parser("run-loop")
    l.add_argument("--patches", required=True)
    l.add_argument("--max-candidats", type=int, required=True)
    l.add_argument("--max-confirmations", type=int, required=True)
    l.add_argument("--budget-minutes", type=float, required=True)
    l.add_argument("--workers", type=int, default=1)
    l.set_defaults(fn=cmd_run_loop)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

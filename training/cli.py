"""Interface unique du harnais d'amelioration du scoring.

    doctor              prerequis, empreintes, modes/coeurs et donnees disponibles
    init-campaign       registre et manifeste de campagne, SANS lancer la boucle
    calibrate           pilote de debit, mesure reelle, 1 ou plusieurs workers
    register-candidate  patch ou bundle -> commit immuable sur scoring-candidates/<id>
    evaluate            etapes de crible (s1/s2/s3) puis confirmation
    report              resultats, incertitudes, couts, raisons de decision
    audit-br            remplacement focal en battle royale, rapport sans veto
    resume              reconcilie ou abandonne une publication interrompue
    run-loop            boucle bornee et COMPLETE : proposition -> S1 -> S2 -> S3 ->
                        confirmation -> promotion, sous trois budgets obligatoires
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics as stdstats
import sys
import tempfile
import threading
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

# En dessous de ce reste de budget, une etape n'est plus lancee : la commencer pour la couper
# aussitot depenserait des combats sans produire de decision.
PLANCHER_ETAPE_SECONDES = 60.0


def charger_config(chemin: Path = CONFIG) -> dict:
    cfg = yaml.safe_load(Path(chemin).read_text(encoding="utf-8"))
    appliquer_profil(cfg)
    return cfg


def appliquer_profil(cfg: dict) -> None:
    """Branche de publication, prefixe de tag et fichier de builds de la campagne.

    Absents de la configuration, ils gardent leurs valeurs historiques (`scoring`, snapshot du
    meta) : une campagne ancienne se relit a l'identique."""
    pub = cfg.get("publication") or {}
    mod_pub.BRANCHE = pub.get("branche", "scoring")
    mod_pub.PREFIXE_TAG = pub.get("prefixe_tag", "scoring")
    source = (cfg.get("builds") or {}).get("source")
    mod_sc.BUILDS = (RACINE.parent / source) if source else (RACINE / "data" / "meta_builds.jsonl")


def _contexte(args):
    cfg = charger_config(Path(args.config))
    moteur = mod_eval.Moteur.detecter(cfg["moteur"]["racine"], cfg["moteur"]["java_home"])
    build = mod_eval.compiler_runner(moteur, RUNS / ".build")
    return cfg, moteur, build


def deployer_bundle(moteur, commit: str, sha256: str) -> tuple[Path, str]:
    """Materialise un bundle SOUS LA RACINE DU GENERATEUR et rend son chemin RELATIF.

    Le `NativeFileSystem` du compilateur LeekScript resout depuis sa propre racine et refuse
    tout ce qui en sort (`resolveSafe`). Un bundle place ailleurs ne provoque AUCUNE erreur de
    lancement : le combat se joue et l'IA leve a chaque tour. Symptome mesure : 128 erreurs par
    combat, 65 tours, quatre dixiemes de seconde — sans regarder le detail, on prend ca pour un
    debit exceptionnel. D'ou le diagnostic de chargement remonte par le runner.

    Le repertoire est nomme par l'empreinte : le generateur ressert un binaire compile quand un
    nom a deja servi, et ici un nom identique signifie un contenu identique.
    """
    racine = moteur.racine / "test" / "ai" / "bundles" / sha256[:16]
    mod_bundle.materialiser(commit, racine)
    return racine, "test/ai/bundles/%s/Main.leek" % sha256[:16]


def _panel(cfg) -> list:
    """Le PANEL de la campagne : la ligue, dans un ordre fige. Les sous-ensembles d'etapes en
    sont des prefixes, ce qui rend les blocs de S1 inclus dans ceux de S2, et ainsi de suite."""
    return mod_ligue.charger(cfg["ligue"]["source"], cfg["ligue"].get("ancre"))


def empreinte_protocole(cfg, moteur, builds, panel) -> tuple[str, dict]:
    """Tout ce qui, en changeant, rendrait les resultats deja obtenus incomparables.

    Le manifeste de campagne etait descriptif : les evaluations rechargeaient la configuration,
    la ligue et les builds courants sans jamais comparer quoi que ce soit. Cette empreinte est
    VERIFIEE avant chaque evaluation.
    """
    detail = {
        "config": hashlib.sha256(json.dumps(cfg, sort_keys=True, ensure_ascii=False)
                                 .encode("utf-8")).hexdigest(),
        "moteur": moteur.empreinte,
        "runner": mod_eval.empreinte_runner(),
        "parseur": mod_eval.VERSION_PARSEUR,
        "builds": mod_sc.empreinte_builds(builds),
        "panel": [{"id": p.ident, "sha256": p.sha256} for p in panel],
        "formats": {n: [f.type_moteur, f.contexte, f.poireaux_par_camp, f.eleveurs_par_camp]
                    for n, f in sorted(mod_sc.FORMATS.items())},
        "tours_max": mod_sc.TOURS_MAX,
    }
    brut = json.dumps(detail, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(brut.encode("utf-8")).hexdigest(), detail


def _graine_campagne(cfg) -> int:
    return int(hashlib.sha256(cfg["campagne"]["id"].encode()).hexdigest()[:8], 16)


ORDRE_ETAPES = ("s1", "s2", "s3")


def controler_faisabilite(cfg, panel, builds) -> list[str]:
    """Le plan demande est-il ATTEIGNABLE ? Rend la liste des impossibilites.

    Un bloc porte UN adversaire. Demander quatre blocs team contre cinq adversaires laisse donc
    forcement un adversaire sans donnees, et le controle de couverture — qui a raison — rend
    INCOMPLET meme quand tous les combats demandes se terminent. La configuration livree avait
    exactement ce defaut, et aucun candidat ne pouvait franchir S2.

    On verifie aussi l'emboitement REEL des tailles retenues : ce que le plan de blocs promet
    doit se constater sur les nombres de la campagne, pas seulement en principe.
    """
    problemes: list[str] = []
    noms = [p.ident for p in panel]
    for nom_etape, spec in cfg["etapes"].items():
        adversaires = int(spec.get("adversaires", len(noms)))
        if adversaires > len(noms):
            problemes.append("%s : %d adversaires demandes, la ligue n'en compte que %d"
                             % (nom_etape, adversaires, len(noms)))
            continue
        for format_nom, nb in (spec.get("blocs") or {}).items():
            if not nb:
                continue
            if format_nom not in mod_sc.FORMATS:
                problemes.append("%s : format inconnu %s" % (nom_etape, format_nom))
            elif nb < adversaires:
                problemes.append(
                    "%s / %s : %d blocs pour %d adversaires — un bloc porte UN adversaire, "
                    "il en manquera %d et la couverture rendra INCOMPLET"
                    % (nom_etape, format_nom, nb, adversaires, adversaires - nb))

    # Emboitement effectif des etapes de crible, sur les tailles retenues.
    graine = _graine_campagne(cfg)
    for precedente, suivante in zip(ORDRE_ETAPES, ORDRE_ETAPES[1:]):
        if precedente not in cfg["etapes"] or suivante not in cfg["etapes"]:
            continue
        a, b = cfg["etapes"][precedente], cfg["etapes"][suivante]
        for format_nom, nb in (a.get("blocs") or {}).items():
            suite = (b.get("blocs") or {}).get(format_nom, 0)
            if not nb or not suite or format_nom not in mod_sc.FORMATS:
                continue
            try:
                avant = {x.cle() for x in mod_sc.plan_de_blocs(
                    format_nom, noms, nb, graine, builds,
                    adversaires=noms[:int(a.get("adversaires", len(noms)))])}
                apres = {x.cle() for x in mod_sc.plan_de_blocs(
                    format_nom, noms, suite, graine, builds,
                    adversaires=noms[:int(b.get("adversaires", len(noms)))])}
            except ValueError as e:
                problemes.append("%s / %s : plan impossible — %s" % (suivante, format_nom, e))
                continue
            if not avant <= apres:
                problemes.append(
                    "%s / %s : %d bloc(s) de %s ne se retrouvent pas dans %s — le cout annonce "
                    "entre etapes ne serait pas cumulatif"
                    % (suivante, format_nom, len(avant - apres), precedente, suivante))
    return problemes


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

    print("== champion actif ==")
    try:
        with mod_reg.Registre() as reg:
            courant = reg.champion_courant()
            en_cours = reg.publication_en_cours()
        emp = mod_bundle.empreinte(courant["commit_code"])
        concorde = emp["sha256"] == courant["bundle_sha256"]
        print("  id            :", courant["champion_courant"])
        print("  commit        :", courant["commit_code"][:12])
        print("  bundle        :", emp["sha256"][:16], "concordance:", "OUI" if concorde else "NON")
        if en_cours is not None:
            print("  PUBLICATION EN COURS :", en_cours["nouveau_champion"],
                  "etape", en_cours["etape"], "— lancer `resume` avant toute evaluation")
            ok = False
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
            print("  %-7s type=%d contexte=%d camps=2x%d poireaux, %d eleveur(s) par camp%s"
                  % (nom, fmt.type_moteur, fmt.contexte, fmt.poireaux_par_camp,
                     fmt.eleveurs_par_camp, "  SYNTHETIQUE" if fmt.synthetique else ""))
        ok = ok and complets >= 4
    except Exception as e:
        ok = False
        print("  ECHEC :", e)

    print("== ligue (panel de campagne, ordre fige) ==")
    try:
        pols = _panel(cfg)
        print("  politiques    :", len(pols))
        dbl = mod_ligue.doublons(pols)
        if dbl:
            print("  DOUBLONS      :", dbl, "— autant d'adversaires identiques, pas de diversite")
        for i, p in enumerate(pols):
            print("    %2d %-24s %-11s %s" % (i, p.ident, p.origine, p.sha256[:16]))
    except Exception as e:
        ok = False
        print("  ECHEC :", e)

    print("== faisabilite du plan ==")
    try:
        problemes = controler_faisabilite(cfg, pols, builds)
        if problemes:
            ok = False
            for p in problemes:
                print("  IMPOSSIBLE :", p)
        else:
            print("  toutes les etapes sont atteignables et emboitees")
    except Exception as e:
        ok = False
        print("  ECHEC :", e)

    print("== protocole ==")
    try:
        emp, _detail = empreinte_protocole(cfg, moteur, builds, pols)
        print("  empreinte     :", emp[:16])
        with mod_reg.Registre() as reg:
            ligne = reg.campagne(cfg["campagne"]["id"])
        if ligne is None:
            print("  campagne      : %s non enregistree (lancer init-campaign)"
                  % cfg["campagne"]["id"])
        elif (ligne["empreinte_protocole"] or "") == emp:
            print("  campagne      : %s, protocole concordant" % cfg["campagne"]["id"])
        else:
            ok = False
            print("  campagne      : %s, PROTOCOLE DIFFERENT (%s enregistre) — ouvrir une "
                  "nouvelle campagne" % (cfg["campagne"]["id"],
                                         (ligne["empreinte_protocole"] or "?")[:16]))
    except Exception as e:
        ok = False
        print("  ECHEC :", e)

    print("\n", "PRET" if ok else "NON PRET", sep="")
    return 0 if ok else 1


# --------------------------------------------------------------------------------------
def cmd_init_campaign(args) -> int:
    cfg, moteur, _build = _contexte(args)
    pols = _panel(cfg)
    builds = mod_sc.charger_builds()
    problemes = controler_faisabilite(cfg, pols, builds)
    if problemes:
        print("REFUS : le plan de cette campagne est inatteignable.")
        for p in problemes:
            print("   -", p)
        print("\nCorriger les tailles ou le nombre d'adversaires AVANT d'ouvrir la campagne : "
              "un plan infaisable depense des combats pour un verdict INCOMPLET garanti.")
        return 2
    emp_proto, detail = empreinte_protocole(cfg, moteur, builds, pols)
    with mod_reg.Registre() as reg:
        courant = reg.champion_courant()
        conf = json.dumps(cfg, sort_keys=True, ensure_ascii=False)
        manifeste = {
            "id": cfg["campagne"]["id"],
            "cree_le": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "statut": "preparee",
            "champion_initial": courant,
            "panel": mod_ligue.resume(pols),
            "doublons_de_ligue": mod_ligue.doublons(pols),
            "moteur": {"racine": str(moteur.racine), "empreinte": moteur.empreinte,
                       "java": moteur.version_java(),
                       "runner_sha256": mod_eval.empreinte_runner()},
            "politique_coeurs": "reels, aucun plancher ni plafond",
            "tours_max": mod_sc.TOURS_MAX,
            "objectif": cfg["objectif"],
            "etapes": cfg["etapes"],
            "budgets": {"confirmations_max": cfg["campagne"]["confirmations_max"],
                        "risque_nominal_campagne": cfg["campagne"]["risque_nominal_campagne"],
                        "alpha_par_confirmation": cfg["campagne"]["alpha_par_confirmation"]},
            "empreinte_config": hashlib.sha256(conf.encode("utf-8")).hexdigest(),
            "empreinte_protocole": emp_proto,
            "protocole": detail,
            "_note_risque": ("Le risque nominal vaut pour CE budget de confirmations. Enchainer "
                             "des campagnes ne conserve pas 5 % de risque cumule."),
            "_note_gel": ("Le protocole est VERIFIE avant chaque evaluation. Un moteur, des "
                          "builds, une ligue ou une configuration differents refusent de "
                          "continuer cette campagne : c'est une nouvelle campagne."),
            "_note_builds": ("Le snapshot donne des BUILDS, pas les IA des joueurs du haut de "
                             "classement. Les resultats se lisent « contre les politiques de la "
                             "ligue sur des builds representatifs »."),
        }
        try:
            etat = reg.enregistrer_campagne(manifeste["id"], conf, manifeste["empreinte_config"],
                                            emp_proto, detail)
        except mod_reg.ProtocoleDifferent as e:
            print("REFUS :", e)
            return 2
        chemin = RUNS / ("campagne-%s.json" % cfg["campagne"]["id"])
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps(manifeste, ensure_ascii=False, indent=2), encoding="utf-8")
        utilisees = reg.confirmations_utilisees(manifeste["id"])
    print("campagne %s :" % etat, chemin)
    print("champion initial  :", courant["champion_courant"], courant["commit_code"][:12])
    print("panel             :", len(pols), "politiques")
    print("protocole         :", emp_proto[:16])
    print("confirmations     : %d utilisees sur %d"
          % (utilisees, cfg["campagne"]["confirmations_max"]))
    print("\nAucune boucle lancee. Etape suivante : register-candidate puis evaluate, ou run-loop.")
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

        durees, compils, erreurs, valides, ecartes = [], [], {}, 0, []
        for lot, bruts in zip(lots, sorties):
            for chemin, brut in zip(lot, bruts):
                _s, err, det = mod_eval.analyser(brut)
                erreurs[err] = erreurs.get(err, 0) + 1
                # LA fonction de production, celle que le test de reception exerce. Un combat
                # dont les IA n'ont pas ete chargees ne nourrit aucune mesure de debit : il est
                # rapide precisement parce qu'il n'a rien calcule.
                bon, raison = mod_eval.valide_pour_le_debit(brut)
                if not bon:
                    ecartes.append({"fichier": chemin.name, "raison": raison})
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
                "n_valides": valides, "n_ecartes": len(ecartes), "mur_s": round(mur, 2),
                "compilation_max_s": round(max(compils), 2) if compils else None,
                "execution_mediane_s": round(stdstats.median(durees), 2),
                "execution_p90_s": round(tries[max(0, int(0.9 * len(tries)) - 1)], 2),
                "execution_max_s": round(max(durees), 2),
                "combats_par_minute": round(60.0 * valides / mur, 2),
                "erreurs": erreurs, "ecartes": ecartes,
            }
        else:
            par_format[f] = {"n_valides": 0, "n_ecartes": len(ecartes), "erreurs": erreurs,
                             "ecartes": ecartes,
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
class CandidatDivergent(RuntimeError):
    """Un identifiant deja pris designe une AUTRE idee : renommer serait perdre le fil."""


def _empreinte_source(patch=None, bundle_dir=None) -> str:
    """Empreinte de ce qui produit le candidat : le patch, ou l'arbre du bundle."""
    if patch is not None:
        return hashlib.sha256(Path(patch).read_bytes()).hexdigest()
    if bundle_dir is not None:
        return mod_bundle.empreinte_repertoire(Path(bundle_dir))["sha256"]
    return ""


def _depuis_le_registre(ligne) -> dict:
    portee = json.loads(ligne["portee_json"] or "{}")
    return {"id": ligne["id"], "commit": ligne["commit_code"], "parent": ligne["parent"],
            "bundle_sha256": ligne["bundle_sha256"], "hypothese": ligne["hypothese"],
            "branche": mod_cand.branche_de(ligne["id"]),
            "verdict_portee": portee.get("verdict", mod_opt.DANS_PORTEE),
            "hors_portee": portee.get("hors_portee", []),
            "invariants_rompus": portee.get("invariants_rompus", []),
            "autorisation": portee.get("autorisation", ""),
            "empreinte_source": portee.get("empreinte_source", ""),
            "fichiers_modifies": portee.get("fichiers", []),
            "dependances": portee.get("dependances", [])}


def _retrouver_candidat(cfg, reg, ident: str, parent: str, empreinte: str | None,
                        source: str | None = None) -> dict | None:
    """Le candidat deja constitue, qu'il soit au registre ou seulement dans Git.

    La boucle donnait le meme identifiant au meme patch a chaque lancement puis tentait de le
    reenregistrer : la branche existait deja, le candidat etait rejete, et l'avancement deja
    paye devenait inaccessible. Un candidat est immuable — s'il est le meme, on le REPREND.

    Deuxieme cas, plus sournois : le commit et la branche sont crees AVANT l'insertion SQLite.
    Une coupure entre les deux laissait une branche que le registre ignorait, et toute relance
    butait sur « la branche existe deja ». On reconstruit alors le candidat depuis Git et on
    l'inscrit, apres verification de son parent et de l'identite de sa proposition.

    `empreinte` vaut None quand la source n'est pas connue d'avance (fournisseur qu'il faudrait
    appeler pour la connaitre) : seul le parent est alors verifie.
    """
    ligne = reg.candidat(ident)
    if ligne is not None:
        info = _depuis_le_registre(ligne)
        meme_source = empreinte is None or info["empreinte_source"] == empreinte
        if info["parent"] != parent or not meme_source:
            raise CandidatDivergent(
                "le candidat %s existe deja pour une autre idee ou un autre parent. Un candidat "
                "est immuable : choisir un identifiant neuf plutot que d'ecraser un essai deja "
                "mesure." % ident)
        return info

    if not mod_cand.branche_existe(ident):
        return None
    # Une branche sans marqueur de provenance ne peut etre rattachee qu'en DEMONTRANT que le
    # patch demande reconstruit son arbre. Le mode manuel connait son patch sans appel ; un
    # fournisseur qui ne peut pas le fournir verra la reprise refusee, ce qui vaut mieux que
    # d'attribuer une evaluation a une proposition qui n'a pas produit le code mesure.
    with tempfile.TemporaryDirectory() as t:
        preuve = None
        if source is not None:
            preuve = Path(t) / "source.patch"
            preuve.write_bytes(source.encode("utf-8"))
        try:
            recupere = mod_cand.retrouver(ident, parent, empreinte or "", preuve)
        except RuntimeError as e:
            raise CandidatDivergent(str(e))
    # Inscription interrompue : le commit existe, le registre l'ignorait. On le reinscrit tel
    # quel, sans toucher au commit deja cree.
    verdict, fautifs = mod_opt.classer_portee(recupere["fichiers_modifies"],
                                              cfg["optimiseur"]["portee"],
                                              cfg["optimiseur"]["hors_portee"])
    ruptures = mod_opt.verifier_invariants(
        lambda chemin: mod_cand.contenu_au_commit(recupere["commit"], chemin),
        cfg["optimiseur"].get("invariants") or {})
    if ruptures:
        verdict = mod_opt.INVARIANT_ROMPU
    recupere.update({"verdict_portee": verdict, "hors_portee": fautifs,
                     "invariants_rompus": ruptures, "autorisation": "",
                     "hypothese": "(reconstruit depuis Git apres une inscription interrompue)",
                     "dependances": []})
    _inscrire_candidat(cfg, reg, recupere)
    reg.note("candidat", "inscription de %s reconstruite depuis Git : le commit existait, le "
                         "registre l'ignorait." % ident)
    return recupere


def _inscrire_candidat(cfg, reg, info: dict) -> None:
    """L'inscription SQLite, separee de la creation du commit : c'est la seule des deux qui
    puisse manquer apres une coupure, et une reprise doit pouvoir la refaire seule."""
    etat = ("bloque:%s" % info["verdict_portee"]
            if info["verdict_portee"] in mod_opt.VERDICTS_BLOQUANTS else "enregistre")
    reg.enregistrer_candidat(info["id"], cfg["campagne"]["id"], info["commit"], info["parent"],
                             info["bundle_sha256"], info["hypothese"],
                             {"fichiers": info["fichiers_modifies"],
                              "verdict": info["verdict_portee"],
                              "hors_portee": info["hors_portee"],
                              "invariants_rompus": info["invariants_rompus"],
                              "autorisation": info.get("autorisation", ""),
                              "empreinte_source": info.get("empreinte_source", ""),
                              "dependances": info.get("dependances", [])}, etat)


def _enregistrer_candidat(cfg, reg, ident: str, parent: str, patch=None, bundle_dir=None,
                          hypothese: str = "", dependances=None,
                          autorisation: str = "") -> dict:
    empreinte = _empreinte_source(patch, bundle_dir)
    info = mod_cand.enregistrer(
        ident, parent, patch=patch, bundle_dir=bundle_dir, hypothese=hypothese,
        portee=cfg["optimiseur"]["portee"], hors_portee=cfg["optimiseur"]["hors_portee"],
        dependances=dependances or [],
        invariants=cfg["optimiseur"].get("invariants") or {},
        autorisation=autorisation, empreinte_source=empreinte)
    _inscrire_candidat(cfg, reg, info)
    if info["autorisation"]:
        reg.note("portee", "candidat %s : extension autorisee — %s (fichiers : %s)"
                 % (info["id"], info["autorisation"], ", ".join(info["hors_portee"]) or "-"))
    if info["verdict_portee"] in mod_opt.VERDICTS_BLOQUANTS:
        reg.note("portee", "candidat %s bloque (%s) : %s"
                 % (info["id"], info["verdict_portee"],
                    "; ".join(info["invariants_rompus"] or info["hors_portee"])))
    return info


def cmd_register_candidate(args) -> int:
    cfg = charger_config(Path(args.config))
    with mod_reg.Registre() as reg:
        courant = reg.champion_courant()
        parent = args.parent or courant["commit_code"]
        info = _enregistrer_candidat(
            cfg, reg, args.id, parent,
            patch=Path(args.patch) if args.patch else None,
            bundle_dir=Path(args.bundle) if args.bundle else None,
            hypothese=args.hypothese,
            dependances=[d for d in (args.dependances or "").split(",") if d],
            autorisation=args.autoriser_extension or "")
        deja = reg.bundle_deja_evalue(info["bundle_sha256"])
        if deja is not None and deja["id"] != args.id:
            print("ATTENTION : bundle identique au candidat %s. Le rejouer ne mesurerait rien de"
                  " neuf." % deja["id"])
        chemin = RUNS / "candidats" / ("%s.json" % info["id"])
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    if info["verdict_portee"] in mod_opt.VERDICTS_BLOQUANTS:
        print("\n%s : ce candidat NE SERA PAS EVALUE." % info["verdict_portee"])
        for r in (info["invariants_rompus"] or info["hors_portee"]):
            print("   -", r)
        print("   Un helper necessaire peut etre autorise explicitement :"
              " --autoriser-extension \"motif\" (l'autorisation est journalisee).")
        return 3
    return 0


# --------------------------------------------------------------------------------------
def _formats_requis(spec: dict, blocs: dict) -> list[str]:
    demandes = spec.get("formats_requis")
    if demandes:
        return [f for f in demandes if blocs.get(f, 0) > 0]
    return [f for f, n in blocs.items() if n and n > 0] or ["farmer"]


def _evaluer(cfg, moteur, build, reg, cand: dict, etape: str, workers: int,
             blocs_override: dict | None = None, vague: str = mod_sc.VAGUE_DEV,
             echeance: float | None = None, promouvable: bool = False,
             progres=None) -> dict:
    builds = mod_sc.charger_builds()
    panel = _panel(cfg)
    emp_proto, _detail = empreinte_protocole(cfg, moteur, builds, panel)
    reg.verifier_protocole(cfg["campagne"]["id"], emp_proto)

    courant = reg.champion_courant()
    emp_h = mod_bundle.empreinte(courant["commit_code"])
    deployer_bundle(moteur, courant["commit_code"], emp_h["sha256"])
    ia_h = "test/ai/bundles/%s/Main.leek" % emp_h["sha256"][:16]
    deployer_bundle(moteur, cand["commit"], cand["bundle_sha256"])
    ia_c = "test/ai/bundles/%s/Main.leek" % cand["bundle_sha256"][:16]

    spec = dict(cfg["etapes"][etape])
    blocs = blocs_override or spec["blocs"]
    noms_panel = [p.ident for p in panel]
    actifs = panel[:spec.get("adversaires", len(panel))]
    confirmation = etape == "confirmation"

    t0 = time.monotonic()
    par_format, couverture = mod_orch.evaluer_etape(
        reg, moteur, build, builds, noms_panel, actifs, ia_c, cand["bundle_sha256"],
        ia_h, emp_h["sha256"], blocs, _graine_campagne(cfg), RUNS / "matchs" / etape,
        vague=vague, workers=workers, taille_lot=cfg["debit"].get("taille_lot", 8),
        timeout=cfg["debit"]["timeout_lot_secondes"], echeance=echeance, progres=progres)
    mur = time.monotonic() - t0

    alpha = cfg["campagne"]["alpha_par_confirmation"] if confirmation else 0.05
    motif = ""
    if not promouvable:
        motif = ("etape de crible" if not confirmation else
                 "tailles imposees a la main" if blocs_override else
                 "confirmation non enregistree comme tentative")
    dec = mod_stats.decider(par_format, cfg["objectif"]["poids"],
                            cfg["objectif"]["planchers_empiriques"], alpha,
                            cfg["objectif"]["gain_minimal_farmer"],
                            formats_requis=_formats_requis(spec, blocs),
                            couverture=couverture, promouvable=promouvable,
                            motif_non_promouvable=motif)
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
                          "empreinte": emp_proto, "vague": vague,
                          "blocs": blocs, "panel": noms_panel,
                          "adversaires": [p.ident for p in actifs],
                          "formats_requis": _formats_requis(spec, blocs),
                          "promouvable": promouvable,
                          "taille_figee_avant_resultats": not blocs_override},
            "couverture": couverture,
            "resultats": resultats,
            "decision": {"verdict": dec.verdict, "raisons": dec.raisons,
                         "lacunes": dec.lacunes,
                         "objectif_pondere": round(dec.j, 4),
                         "borne_basse_objectif":
                             None if dec.borne_j is None else round(dec.borne_j, 4)}}


def _candidat_du_registre(reg, ident: str) -> dict | None:
    ligne = reg.candidat(ident)
    if ligne is None:
        return None
    portee = json.loads(ligne["portee_json"] or "{}")
    return {"id": ligne["id"], "commit": ligne["commit_code"], "parent": ligne["parent"],
            "bundle_sha256": ligne["bundle_sha256"], "hypothese": ligne["hypothese"],
            "branche": "%s/%s" % (mod_cand.PREFIXE_BRANCHE, ligne["id"]),
            "verdict_portee": portee.get("verdict", mod_opt.DANS_PORTEE),
            "etat": ligne["etat"]}


def _ouvrir_tentative(cfg, moteur, reg, ident: str, panel, builds):
    """(tentative, reprise). Une tentative COUPEE reprend son plan sans consommer de budget."""
    spec = cfg["etapes"]["confirmation"]
    return reg.ouvrir_confirmation(
        cfg["campagne"]["id"], ident, reg.champion_courant()["champion_courant"],
        spec["blocs"], [p.ident for p in panel][:spec["adversaires"]],
        empreinte_protocole(cfg, moteur, builds, panel)[0],
        cfg["campagne"]["confirmations_max"])


def _cloturer_tentative(reg, tentative, verdict: str, rapport: dict | None = None,
                        candidat: dict | None = None) -> None:
    """Clot la tentative AVEC sa decision et l'intention de publication, en une transaction.

    Un verdict INCOMPLET laisse la tentative PARTIELLE : son plan est a moitie paye et se
    reprend. La marquer terminee faisait rouvrir une tentative neuve a chaque coupure, avec
    d'autres graines et une unite de budget en moins.

    Un verdict PROMOUVOIR laisse une intention `a_publier` : tant que la promotion n'a pas
    abouti, une relance la termine au lieu de rejouer la confirmation ou d'oublier le gagnant.
    """
    etat = "close" if verdict != "INCOMPLET" else "partielle"
    if verdict == "PROMOUVOIR":
        suite = mod_reg.PUBLICATION_A_PUBLIER
    elif etat == "close":
        suite = mod_reg.PUBLICATION_SANS_OBJET
    else:
        suite = None
    decision = None
    if rapport is not None:
        decision = {"champion_attendu": rapport["champion"],
                    "decision": rapport["decision"], "protocole": rapport["protocole"],
                    "candidat": candidat}
    reg.cloturer_confirmation(tentative["id"], etat, verdict, decision, rapport, suite)


def _terminer_publication(cfg, moteur, reg, info: dict, tentative, panel, builds):
    """Termine une promotion AUTORISEE mais restee en plan. Rend (etat, promotion ou None).

    La decision complete est relue depuis la tentative, pas depuis un fichier : c'est elle qui
    a ete ecrite dans la meme transaction que la cloture. Avant de publier, on re-verifie les
    trois choses qui pourraient l'avoir perimee — le candidat, le protocole et le champion
    attendu. Une decision perimee est marquee caduque et journalisee, jamais publiee en
    silence sur une autre reference.
    """
    stockee = json.loads(tentative["decision_json"] or "null")
    rapport = json.loads(tentative["rapport_json"] or "null")
    if not stockee or not rapport:
        reg.marquer_publication_confirmation(tentative["id"], mod_reg.PUBLICATION_CADUQUE)
        return "decision_illisible", None

    emp_proto, _d = empreinte_protocole(cfg, moteur, builds, panel)
    if (tentative["empreinte_protocole"] or "") != emp_proto:
        reg.marquer_publication_confirmation(tentative["id"], mod_reg.PUBLICATION_CADUQUE)
        return "protocole_change", None
    courant = reg.champion_courant()["champion_courant"]
    if stockee["champion_attendu"] != courant:
        reg.marquer_publication_confirmation(tentative["id"], mod_reg.PUBLICATION_CADUQUE)
        return "champion_change", None
    candidat = stockee.get("candidat") or info
    if candidat.get("commit") != info.get("commit"):
        reg.marquer_publication_confirmation(tentative["id"], mod_reg.PUBLICATION_CADUQUE)
        return "candidat_different", None

    # Le fichier de rapport est reconstruit depuis l'etat conserve : la coupure a pu tomber
    # avant son ecriture.
    _conserver(reg, cfg, info["id"], "confirmation", rapport, tentative)
    promu = mod_pub.publier(reg, candidat, stockee["champion_attendu"], stockee["decision"],
                            stockee["protocole"], confirmation=tentative["id"])
    return "publiee_par_reprise", promu


def _chemin_rapport(ident: str, etape: str, tentative=None) -> Path:
    """Un rapport par ETAPE, et un par TENTATIVE de confirmation : une reprise ne doit pas
    effacer le rapport de la tentative precedente."""
    if tentative is not None:
        nom = "%s-confirmation-%02d.json" % (ident, tentative["tentative"])
    else:
        nom = "%s-%s.json" % (ident, etape)
    chemin = RUNS / "rapports" / nom
    chemin.parent.mkdir(parents=True, exist_ok=True)
    return chemin


def _conserver(reg, cfg, ident: str, etape: str, rapport: dict, tentative=None) -> Path:
    """Ecrit le rapport COMPLET sur disque et enregistre l'avancement du candidat."""
    chemin = _chemin_rapport(ident, etape, tentative)
    chemin.write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
    reg.enregistrer_avancement(
        cfg["campagne"]["id"], ident, etape, rapport["decision"]["verdict"],
        rapport["decision"]["objectif_pondere"],
        all(c["complet"] for c in rapport["couverture"].values()), rapport)
    return chemin


def cmd_evaluate(args) -> int:
    cfg, moteur, build = _contexte(args)
    promu, chemin = None, None
    with mod_reg.Registre() as reg:
        if reg.publication_en_cours() is not None:
            print("REFUS : une publication est en cours. Lancer `resume` d'abord.")
            return 2
        cand = _candidat_du_registre(reg, args.id)
        if cand is None:
            print("candidat inconnu :", args.id)
            return 2
        if cand["verdict_portee"] in mod_opt.VERDICTS_BLOQUANTS:
            print("REFUS : candidat %s classe %s. Une extension non resolue ne participe ni au "
                  "crible ni a la confirmation." % (args.id, cand["verdict_portee"]))
            return 3

        etape = "confirmation" if args.stage == "confirm" else args.stage
        override = json.loads(args.blocs) if args.blocs else None
        vague = mod_sc.VAGUE_DEV
        tentative = None
        if etape == "confirmation":
            if override is not None and args.promouvoir:
                print("REFUS : --blocs impose des tailles a la main. Une confirmation publiable "
                      "utilise les tailles figees du protocole.")
                return 2
            # Une decision POSITIVE encore a publier se TERMINE ici, comme dans la boucle :
            # ouvrir une tentative de plus rejouerait tout le plan sur d'autres graines et
            # consommerait une unite de budget pour recalculer ce qui est deja decide. Une
            # demande VOLONTAIRE d'un nouvel echantillon reste possible, mais elle se dit.
            a_publier = reg.confirmation_a_publier(cfg["campagne"]["id"], args.id)
            if a_publier is not None and not args.nouvelle_tentative:
                if not args.promouvoir:
                    print("REFUS : une decision %s attend sa publication (tentative %s). "
                          "Ajouter --promouvoir pour la terminer, ou --nouvelle-tentative pour "
                          "demander explicitement un nouvel echantillon."
                          % (a_publier["verdict"], a_publier["vague"]))
                    return 2
                panel = _panel(cfg)
                etat_reprise, promu = _terminer_publication(
                    cfg, moteur, reg, cand, a_publier, panel, mod_sc.charger_builds())
                print("reprise de la tentative %s : %s" % (a_publier["vague"], etat_reprise))
                if promu is None:
                    print("La decision conservee n'est plus publiable. Relancer une "
                          "confirmation avec --nouvelle-tentative si l'idee tient toujours.")
                    return 1
                print("PROMU :", promu["champion"], promu["tag"])
                return 0
            if override is None:
                try:
                    tentative, reprise = _ouvrir_tentative(
                        cfg, moteur, reg, args.id, _panel(cfg), mod_sc.charger_builds())
                except (mod_reg.BudgetEpuise, mod_reg.ProtocoleDifferent,
                        mod_reg.ChampionObsolete) as e:
                    print("REFUS :", e)
                    return 2
                vague = tentative["vague"]
                print("tentative de confirmation %s (%s) : vague %s"
                      % (tentative["tentative"], "reprise" if reprise else "nouvelle", vague))

        promouvable = etape == "confirmation" and tentative is not None
        try:
            rapport = _evaluer(cfg, moteur, build, reg, cand, etape, args.workers, override,
                               vague=vague, promouvable=promouvable)
        except mod_reg.ProtocoleDifferent as e:
            print("REFUS :", e)
            return 2
        if override is None:
            chemin = _conserver(reg, cfg, args.id, etape, rapport, tentative)
        else:
            chemin = _chemin_rapport(args.id, "%s-lot-technique" % etape)
            chemin.write_text(json.dumps(rapport, ensure_ascii=False, indent=2),
                              encoding="utf-8")

        if tentative is not None:
            _cloturer_tentative(reg, tentative, rapport["decision"]["verdict"], rapport, cand)
        if etape == "confirmation" and rapport["decision"]["verdict"] == "PROMOUVOIR" \
                and args.promouvoir:
            promu = mod_pub.publier(reg, cand, rapport["champion"], rapport["decision"],
                                    rapport["protocole"],
                                    confirmation=tentative["id"] if tentative else None)
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
        print("%s | etape %s | champion %s | alpha %s | %s"
              % (r["candidat"]["id"], r["etape"], r["champion"], r["alpha"],
                 "PROMOUVABLE" if r["protocole"].get("promouvable") else "non promouvable"))
        print("hypothese :", r["candidat"].get("hypothese") or "(aucune)")
        for f, v in r["resultats"].items():
            cv = (r.get("couverture") or {}).get(f, {})
            print("  %-7s blocs %4d/%-4s  delta %+.4f  borne %s  ddl %s"
                  % (f, v["blocs"], cv.get("blocs_attendus", "?"), v["delta_moyen"],
                     v["borne_basse"], v["ddl_welch"]))
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
        pols = _panel(cfg)
        rapport = mod_orch.auditer_br(reg, moteur, build, builds, pols, ia_c, sha_c,
                                      ia_h, emp_h["sha256"], RUNS / "matchs" / "br",
                                      lobbies=args.lobbies,
                                      creneaux=cfg["br"]["creneaux_focaux_par_lobby"],
                                      graine=_graine_campagne(cfg),
                                      workers=args.workers,
                                      taille_lot=cfg["debit"].get("taille_lot", 8),
                                      timeout=cfg["debit"]["timeout_lot_secondes"])
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
    return 0 if etat["etat"] in ("rien_a_reconcilier", "terminee_par_reprise",
                                 "abandonnee") else 1


# --------------------------------------------------------------------------------------
class Budgets:
    """Les trois budgets de la boucle, RESERVES et decomptes au fil de la consommation."""

    def __init__(self, max_candidats: int, max_confirmations: int, minutes: float):
        self.max_candidats = max_candidats
        self.max_confirmations = max_confirmations
        self.echeance = time.monotonic() + minutes * 60.0
        self.candidats = 0
        self.confirmations = 0
        self.combats = 0
        # Les workers rapportent leur avancement depuis plusieurs fils : le compte des combats
        # consommes doit etre exact, c'est lui qu'on rapporte.
        self.verrou = threading.Lock()

    def un_combat_de_plus(self) -> None:
        with self.verrou:
            self.combats += 1

    @property
    def reste_secondes(self) -> float:
        return self.echeance - time.monotonic()

    def place_pour_une_etape(self) -> bool:
        return self.reste_secondes > PLANCHER_ETAPE_SECONDES

    def etat(self) -> dict:
        return {"candidats": "%d/%d" % (self.candidats, self.max_candidats),
                "confirmations": "%d/%d" % (self.confirmations, self.max_confirmations),
                "secondes_restantes": round(max(0.0, self.reste_secondes), 1),
                "combats_joues": self.combats}


def _objectif(avancement: dict, ident: str, etape: str) -> float:
    """L'objectif pondere mesure a cette etape, ou moins l'infini si elle n'a pas abouti."""
    ligne = (avancement.get(ident) or {}).get(etape)
    if ligne is None or ligne["verdict"] == "INCOMPLET" or ligne["objectif"] is None:
        return float("-inf")
    return float(ligne["objectif"])


def _resultats_connus(reg, cfg) -> list[dict]:
    """Le retour de resultats rendu a l'optimiseur : ce qui a deja ete mesure dans la
    campagne, etape par etape."""
    lignes = reg.executer(
        "SELECT candidat, etape, verdict, objectif FROM avancement WHERE campagne=?"
        " ORDER BY horodatage", (cfg["campagne"]["id"],)).fetchall()
    return [dict(l) for l in lignes]


def _propositions(cfg, source: Path) -> list[tuple[str, object]]:
    """(identifiant, optimiseur) pour chaque proposition de la vague.

    Le mode manuel reste un vrai optimiseur : chaque patch du repertoire devient un `Manuel`
    dont `proposer` est REELLEMENT appele par la boucle. Brancher un fournisseur paye ne
    changera que cette fonction.
    """
    if not source.is_dir():
        return []
    sorties = []
    for p in sorted(source.glob("*.patch")):
        sorties.append((p.stem, mod_opt.Manuel(p.read_text(encoding="utf-8"), p.stem)))
    return sorties


def cmd_run_loop(args) -> int:
    """Boucle BORNEE et COMPLETE : proposition -> S1 -> selection -> S2 -> S3 ->
    confirmation -> promotion. Les trois budgets sont obligatoires.

    La confirmation et la promotion sont AUTOMATIQUES : leur cout est encadre par les budgets,
    et les laisser manuelles laissait la boucle s'arreter au crible. Ce qui reste interdit
    n'est pas la transition, c'est de promouvoir sur un protocole incomplet ou non fige — et
    c'est le decideur qui l'empeche.
    """
    cfg, moteur, build = _contexte(args)
    budgets = Budgets(args.max_candidats, args.max_confirmations, args.budget_minutes)
    source = Path(args.patches)
    propositions = _propositions(cfg, source)
    journal: list[dict] = []
    promotions: list[dict] = []

    if not propositions:
        print("aucun patch dans %s : le mode manuel attend des fichiers .patch." % source)
        return 2

    def _compter(faits, total):
        if faits:
            budgets.un_combat_de_plus()

    with mod_reg.Registre() as reg:
        if reg.publication_en_cours() is not None:
            etat = mod_pub.reconcilier(reg)
            journal.append({"etape": "reprise", "etat": etat})
            if etat["etat"] not in ("terminee_par_reprise", "abandonnee", "rien_a_reconcilier"):
                print("publication irrecuperable :", json.dumps(etat, ensure_ascii=False))
                return 1

        builds = mod_sc.charger_builds()
        panel = _panel(cfg)
        emp_proto, _d = empreinte_protocole(cfg, moteur, builds, panel)
        try:
            reg.verifier_protocole(cfg["campagne"]["id"], emp_proto)
        except mod_reg.ProtocoleDifferent as e:
            print("REFUS :", e)
            return 2

        # ---- 1. La vague : un candidat par proposition, REPRIS s'il existe deja ----
        avancement: dict[str, dict] = {}
        vague: list[dict] = []
        for ident_court, optimiseur in propositions:
            champion = reg.champion_courant()
            ident = "%s-%s" % (cfg["campagne"]["id"], ident_court)
            # L'empreinte de la source AVANT d'appeler l'optimiseur : le mode manuel connait
            # deja son patch, donc reconnaitre un candidat existant ne coute aucun appel.
            prevue = getattr(optimiseur, "empreinte_prevue", lambda: None)()
            source = getattr(optimiseur, "source", lambda: None)()
            try:
                info = _retrouver_candidat(cfg, reg, ident, champion["commit_code"], prevue,
                                           source)
            except CandidatDivergent as e:
                journal.append({"proposition": ident_court, "etat": "rejete",
                                "detail": str(e)[:200]})
                continue
            if info is not None:
                # Candidat IMMUABLE deja constitue : on le reprend avec son avancement, sans
                # consommer une unite de budget pour un travail deja paye, et sans demander
                # une proposition de plus.
                journal.append({"candidat": ident, "etat": "repris",
                                "detail": "candidat deja enregistre, avancement recupere"})
            else:
                # Les budgets se verifient AVANT de demander une proposition : un plafond
                # atteint ou un temps epuise ne doit produire ni appel a l'optimiseur, ni
                # commit, ni inscription. La boucle creait au contraire un candidat complet
                # avec zero minute de budget, pour refuser S1 juste apres.
                if budgets.candidats >= budgets.max_candidats:
                    journal.append({"proposition": ident_court, "etat": "non_traite",
                                    "detail": "plafond de candidats atteint"})
                    continue
                if not budgets.place_pour_une_etape():
                    journal.append({"proposition": ident_court, "etat": "non_traite",
                                    "detail": "budget de temps epuise"})
                    continue
                proposition = optimiseur.proposer({
                    "champion": champion["champion_courant"],
                    "commit_champion": champion["commit_code"],
                    "portee": cfg["optimiseur"]["portee"],
                    "hors_portee": cfg["optimiseur"]["hors_portee"],
                    "invariants": cfg["optimiseur"].get("invariants") or {},
                    "objectif": cfg["objectif"],
                    # L'echeance voyage avec la demande : une verification entre deux etapes
                    # ne borne pas un appel externe bloquant.
                    "secondes_restantes": round(max(0.0, budgets.reste_secondes), 1),
                    "resultats_precedents": _resultats_connus(reg, cfg),
                })
                fichier = RUNS / "propositions" / ("%s.patch" % ident)
                fichier.parent.mkdir(parents=True, exist_ok=True)
                # En OCTETS : l'ecriture texte de Python traduit les fins de ligne sous
                # Windows, et un diff aux fins de ligne reecrites ne s'applique sur rien.
                fichier.write_bytes(proposition["patch"].encode("utf-8"))
                # L'appel a pu CONSOMMER le temps restant — un fournisseur externe repondra
                # bien plus lentement qu'une lecture de fichier. La proposition est conservee
                # pour une reprise, mais aucune operation Git n'est engagee apres l'echeance.
                if not budgets.place_pour_une_etape():
                    journal.append({"proposition": ident_court, "etat": "conservee",
                                    "detail": "budget epuise pendant l'appel a l'optimiseur ; "
                                              "patch conserve, aucune inscription",
                                    "patch": str(fichier)})
                    continue
                try:
                    info = _enregistrer_candidat(cfg, reg, ident, champion["commit_code"],
                                                 patch=fichier,
                                                 hypothese=proposition["hypothese"],
                                                 dependances=proposition.get("dependances"))
                except Exception as e:
                    journal.append({"proposition": ident_court, "etat": "rejete",
                                    "detail": str(e)[:200]})
                    continue
                budgets.candidats += 1
            if info["verdict_portee"] in mod_opt.VERDICTS_BLOQUANTS:
                journal.append({"candidat": ident, "etat": "bloque",
                                "verdict": info["verdict_portee"],
                                "detail": info["invariants_rompus"] or info["hors_portee"]})
                continue
            avancement[ident] = {e: dict(r) for e, r in
                                 reg.avancement(cfg["campagne"]["id"], ident).items()}
            vague.append(info)

        # ---- 2. Les etapes de crible, reprises la ou elles s'etaient arretees ----
        etape_precedente = {"s2": "s1", "s3": "s2"}
        retenus = vague
        for etape in ORDRE_ETAPES:
            if etape not in cfg["etapes"]:
                continue
            if etape in etape_precedente:
                # Le nombre de survivants est fixe par l'etape PRECEDENTE : c'est elle qui a
                # mesure, c'est elle qui dit combien de candidats meritent de couter plus cher.
                garder = cfg["etapes"][etape_precedente[etape]].get("garder", 1)
                precedente = etape_precedente[etape]
                retenus.sort(key=lambda c: _objectif(avancement, c["id"], precedente),
                             reverse=True)
                retenus = [c for c in retenus
                           if _objectif(avancement, c["id"], precedente) > 0][:garder]
            suite = []
            for info in retenus:
                deja = avancement[info["id"]].get(etape)
                if deja is not None and deja["verdict"] != "INCOMPLET":
                    journal.append({"candidat": info["id"], "etape": etape, "etat": "repris",
                                    "decision": deja["verdict"], "objectif": deja["objectif"]})
                    suite.append(info)
                    continue
                if not budgets.place_pour_une_etape():
                    journal.append({"candidat": info["id"], "etape": etape,
                                    "etat": "non_traite", "detail": "budget de temps epuise"})
                    continue
                r = _evaluer(cfg, moteur, build, reg, info, etape, args.workers,
                             echeance=budgets.echeance, progres=_compter)
                _conserver(reg, cfg, info["id"], etape, r)
                avancement[info["id"]][etape] = {
                    "verdict": r["decision"]["verdict"],
                    "objectif": r["decision"]["objectif_pondere"]}
                journal.append({"candidat": info["id"], "etape": etape,
                                "decision": r["decision"]["verdict"],
                                "objectif": r["decision"]["objectif_pondere"],
                                "complet": all(c["complet"]
                                               for c in r["couverture"].values())})
                if r["decision"]["verdict"] != "INCOMPLET":
                    suite.append(info)
            retenus = suite

        # ---- 3. Confirmation puis promotion ----
        derniere = ORDRE_ETAPES[-1]
        finalistes = sorted([c for c in retenus
                             if _objectif(avancement, c["id"], derniere) > 0],
                            key=lambda c: _objectif(avancement, c["id"], derniere),
                            reverse=True)
        for info in finalistes:
            # a. Une decision POSITIVE dont la promotion n'a pas abouti se TERMINE, sans
            #    rejouer un seul combat. C'est la tentative close qui porte cette intention,
            #    pas le fichier de rapport : une coupure entre la cloture et la conservation,
            #    ou une publication abandonnee pour raison technique, faisait auparavant
            #    oublier le candidat gagnant a chaque relance.
            a_publier = reg.confirmation_a_publier(cfg["campagne"]["id"], info["id"])
            if a_publier is not None:
                etat_reprise, promu = _terminer_publication(cfg, moteur, reg, info, a_publier,
                                                            panel, builds)
                journal.append({"candidat": info["id"], "etape": "promotion",
                                "etat": etat_reprise, "reprise": True,
                                "tentative": a_publier["vague"], "promu": promu})
                if promu is None:
                    continue
                promotions.append(promu)
                journal.append({"etape": "fin_de_vague",
                                "detail": "promotion terminee depuis une decision conservee"})
                break

            # b. Une decision deja rendue et deja suivie d'effet ne se rejoue pas : ce serait
            #    ouvrir une seconde tentative, sur des graines neuves, et consommer le budget
            #    de la campagne en silence. En redemander une est un choix explicite.
            decidee = reg.confirmation_decidee(cfg["campagne"]["id"], info["id"])
            if decidee is not None:
                journal.append({"candidat": info["id"], "etape": "confirmation",
                                "etat": "repris", "decision": decidee["verdict"],
                                "tentative": decidee["vague"],
                                "suite": decidee["publication_etat"],
                                "detail": "confirmation deja decidee pour ce champion"})
                continue

            if not budgets.place_pour_une_etape():
                journal.append({"candidat": info["id"], "etape": "confirmation",
                                "etat": "non_traite", "detail": "budget de temps epuise"})
                continue
            # c. Une tentative REPRISE ne consomme pas de budget : son plan est deja paye, il
            #    s'agit de le terminer. Seule une tentative NEUVE en consomme une — et le
            #    plafond se verifie AVANT de l'ouvrir, faute de quoi une tentative vide serait
            #    inscrite au registre sans qu'aucun combat ne soit joue.
            reprenable = reg.confirmation_reprenable(cfg["campagne"]["id"], info["id"])
            if reprenable is None and budgets.confirmations >= budgets.max_confirmations:
                journal.append({"candidat": info["id"], "etape": "confirmation",
                                "etat": "non_traite", "detail": "plafond de confirmations"})
                continue
            try:
                tentative, reprise = _ouvrir_tentative(cfg, moteur, reg, info["id"], panel,
                                                       builds)
            except (mod_reg.BudgetEpuise, mod_reg.ProtocoleDifferent,
                    mod_reg.ChampionObsolete) as e:
                journal.append({"candidat": info["id"], "etape": "confirmation",
                                "etat": "refuse", "detail": str(e)[:200]})
                continue
            if not reprise:
                budgets.confirmations += 1
            r = _evaluer(cfg, moteur, build, reg, info, "confirmation", args.workers,
                         vague=tentative["vague"], echeance=budgets.echeance,
                         promouvable=True, progres=_compter)
            verdict = r["decision"]["verdict"]
            # La cloture porte la decision ET l'intention de publication, en une transaction.
            _cloturer_tentative(reg, tentative, verdict, r, info)
            _conserver(reg, cfg, info["id"], "confirmation", r, tentative)
            journal.append({"candidat": info["id"], "etape": "confirmation",
                            "tentative": tentative["vague"], "reprise": reprise,
                            "decision": verdict, "lacunes": r["decision"]["lacunes"]})
            if verdict != "PROMOUVOIR":
                continue
            promu = mod_pub.publier(reg, info, r["champion"], r["decision"],
                                    r["protocole"], confirmation=tentative["id"])
            promotions.append(promu)
            journal.append({"candidat": info["id"], "etape": "promotion", "promu": promu})
            # Le champion a change : les resultats des autres finalistes comparent a l'ancienne
            # reference. La vague s'arrete ici, la suivante repartira du nouveau champion.
            journal.append({"etape": "fin_de_vague",
                            "detail": "promotion effectuee ; les comparaisons restantes "
                                      "portaient sur l'ancien champion"})
            break

    etat = {"propositions": len(propositions),
            "candidats_enregistres": budgets.candidats,
            "confirmations_lancees": budgets.confirmations,
            "promotions": len(promotions),
            "budgets": budgets.etat(),
            "journal": journal,
            "_note": ("Confirmation et promotion sont automatiques et bornees par les budgets. "
                      "Ce qui reste interdit est de promouvoir sur un protocole incomplet ou "
                      "non fige : le decideur rend INCOMPLET, et la publication n'a pas lieu.")}
    # Un fichier par LANCEMENT, plus une copie du dernier. L'ancien fichier unique etait
    # remplace au lancement suivant : la trace du parcours interrompu disparaissait avec lui,
    # alors que c'est precisement celle qu'on veut relire apres une coupure. Les rapports
    # complets de chaque etape vivent, eux, dans runs/rapports et dans le registre.
    horodatage = time.strftime("%Y%m%dT%H%M%S")
    archive = RUNS / "boucles" / ("boucle-%s.json" % horodatage)
    archive.parent.mkdir(parents=True, exist_ok=True)
    contenu = json.dumps(etat, ensure_ascii=False, indent=2)
    archive.write_text(contenu, encoding="utf-8")
    chemin = RUNS / "rapport-boucle.json"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(contenu, encoding="utf-8")
    print(contenu)
    print("\narchive du lancement :", archive)
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
    r.add_argument("--autoriser-extension", default="",
                   help="motif d'autorisation d'un helper hors perimetre ; journalise")
    r.set_defaults(fn=cmd_register_candidate)

    e = s.add_parser("evaluate")
    e.add_argument("--id", required=True)
    e.add_argument("--stage", default="s1", choices=["s1", "s2", "s3", "confirm"])
    e.add_argument("--workers", type=int, default=1)
    e.add_argument("--blocs", default=None,
                   help='tailles imposees, ex: {"farmer":2,"solo":1} — lot NON promouvable')
    e.add_argument("--promouvoir", action="store_true")
    e.add_argument("--nouvelle-tentative", action="store_true",
                   help="demander explicitement un nouvel echantillon alors qu'une decision "
                        "attend sa publication ; consomme une unite de budget")
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

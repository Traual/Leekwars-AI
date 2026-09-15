"""Rejeu hors ligne du crible progressif sur les resultats COMPLETS de campagne-003.

Usage :
    python training/v3/rejeu_crible.py extraire          # blocs d, dans l'ordre du plan
    python training/v3/rejeu_crible.py variances         # variance intra-adversaire par format
    python training/v3/rejeu_crible.py simuler <config>  # paliers et seuils d'une configuration

Ces resultats evaluent le PROCEDE (economies, rejets qui auraient ete faits a tort) ; ils ne
mesurent pas le niveau de jeu en 3.00, et une regle ajustee sur ces traces n'est pas validee
independamment par elles.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import sys
from pathlib import Path

ICI = Path(__file__).resolve().parent
TRAINING = ICI.parent
sys.path.insert(0, str(TRAINING))

import cli                      # noqa: E402
import scenarios as mod_sc      # noqa: E402

SORTIE = TRAINING / "runs" / "v3" / "rejeu"
REGISTRE = TRAINING / "runs" / "registre.sqlite"
CHAMPIONS = {
    "champion-000": "f74673365897f79c88aee94843069e8dc3971a8a96a98ae2e2632accc8176935",
    "champion-001": "4e50d520fd8c4f0213fa9aedd6997799ae455b708cf882a34ee68161d0662a2d",
}


def _bundle_path(sha: str) -> str:
    return "test/ai/bundles/%s/Main.leek" % sha[:16]


def extraire() -> None:
    cx = sqlite3.connect(REGISTRE)
    lignes = cx.execute("SELECT bloc, orientation, politique_gauche, politique_droite, score_gauche, "
                        "erreur FROM matchs").fetchall()
    par_bloc: dict[str, list] = {}
    for bloc, o, g, d, s, e in lignes:
        par_bloc.setdefault(bloc, []).append((o, g, d, s, e))
    builds = mod_sc.charger_builds(TRAINING / "data" / "meta_builds.jsonl")
    graine = cli._graine_campagne({"campagne": {"id": "campagne-003"}})
    rapports = sorted((TRAINING / "runs" / "rapports").glob("campagne-003-*.json"))
    donnees = []
    for chemin in rapports:
        r = json.loads(chemin.read_text(encoding="utf-8"))
        if "decision" not in r:
            continue
        proto = r["protocole"]
        panel = proto["panel"]
        actifs = proto["adversaires"]
        sha_c = r["candidat"]["bundle_sha256"]
        sha_h = CHAMPIONS[r["champion"]]
        ia_c, ia_h = _bundle_path(sha_c), _bundle_path(sha_h)
        entree = {"candidat": r["candidat"]["id"], "etape": r["etape"], "vague": proto["vague"],
                  "champion": r["champion"], "verdict": r["decision"]["verdict"],
                  "objectif": r["decision"]["objectif_pondere"], "formats": {}}
        for fmt, nb in proto["blocs"].items():
            if not nb:
                continue
            blocs = mod_sc.plan_de_blocs(fmt, panel, nb, graine, builds, vague=proto["vague"],
                                         adversaires=actifs)
            suite = []
            for b in blocs:
                # Les combats se reconnaissent par la politique et le cote, pas par l'etiquette
                # d'orientation : un combat champion contre adversaire a pu etre joue d'abord
                # comme combat de CANDIDAT (meme code, meme cle), et garde cette etiquette.
                scores = {}
                for o, g, d, s, e in par_bloc.get(b.cle(), []):
                    if s is None:
                        continue
                    if g == ia_c:
                        scores["co"] = s
                    elif d == ia_c:
                        scores["oc"] = s
                    if g == ia_h:
                        scores["ho"] = s
                    elif d == ia_h:
                        scores["oh"] = s
                if len(scores) == 4:
                    x_c = (scores["co"] + (1 - scores["oc"])) / 2
                    x_h = (scores["ho"] + (1 - scores["oh"])) / 2
                    suite.append({"indice": b.indice, "adversaire": b.adversaire, "d": x_c - x_h})
                else:
                    suite.append({"indice": b.indice, "adversaire": b.adversaire, "d": None,
                                  "orientations": sorted(scores)})
            entree["formats"][fmt] = suite
        donnees.append(entree)
    SORTIE.mkdir(parents=True, exist_ok=True)
    (SORTIE / "blocs_campagne003.json").write_text(json.dumps(donnees, indent=1), encoding="utf-8")
    for e in donnees:
        manquants = {f: sum(1 for x in s if x["d"] is None) for f, s in e["formats"].items()}
        print("%-50s %-12s %-12s blocs %s manquants %s"
              % (e["candidat"], e["etape"], e["verdict"],
                 {f: len(s) for f, s in e["formats"].items()}, manquants))


def variances() -> dict:
    donnees = json.loads((SORTIE / "blocs_campagne003.json").read_text(encoding="utf-8"))
    par_format: dict[str, list[tuple[float, int]]] = {}
    for e in donnees:
        for fmt, suite in e["formats"].items():
            par_adv: dict[str, list[float]] = {}
            for x in suite:
                if x["d"] is not None:
                    par_adv.setdefault(x["adversaire"], []).append(x["d"])
            for ds in par_adv.values():
                if len(ds) >= 2:
                    par_format.setdefault(fmt, []).append((statistics.variance(ds), len(ds) - 1))
    sortie = {}
    for fmt, liste in par_format.items():
        ddl = sum(k for _v, k in liste)
        poolee = sum(v * k for v, k in liste) / ddl
        sortie[fmt] = {"variance_poolee": round(poolee, 4), "ddl": ddl, "groupes": len(liste)}
    print(json.dumps(sortie, indent=1))
    (SORTIE / "variances_campagne003.json").write_text(json.dumps(sortie, indent=1), encoding="utf-8")
    return sortie


PALIERS_REJEU = {
    # Paliers ramenes aux effectifs des etapes HISTORIQUES : le rejeu ne peut couper qu'a
    # l'interieur de ce qui a ete joue.
    "s1": [{"solo": 4}, {"solo": 4, "farmer": 4}, {"solo": 4, "farmer": 8}],
    "s2": [{"solo": 8, "farmer": 12}, {"solo": 8, "farmer": 24, "team": 5}],
    "s3": [{"solo": 20, "farmer": 30, "team": 10}, {"solo": 20, "farmer": 60, "team": 10}],
    "confirmation": [{"farmer": 80, "solo": 30, "team": 30}, {"farmer": 160, "solo": 60, "team": 60}],
}


def simuler(crible: dict, sortie: bool = True) -> dict:
    import progressif as prog
    import statistics_lab as st
    donnees = json.loads((SORTIE / "blocs_campagne003.json").read_text(encoding="utf-8"))
    cfg = {"objectif": {"poids": {"farmer": 0.80, "solo": 0.15, "team": 0.05},
                        "planchers_empiriques": {"solo": -0.05, "team": -0.05},
                        "gain_minimal_farmer": 0.01},
           "crible": crible}
    # Issue historique de chaque candidat : la derniere etape atteinte et son objectif.
    atteint: dict[str, dict] = {}
    for e in donnees:
        atteint.setdefault(e["candidat"], {})[e["etape"]] = e
    bilan = {"etapes": 0, "arrets": 0, "combats_c_prevus": 0, "combats_c_evites": 0,
             "arrets_a_tort": [], "detail": []}
    for e in donnees:
        etape = e["etape"]
        paliers = prog.paliers_de({"blocs": {f: len(s) for f, s in e["formats"].items()},
                                   "paliers": PALIERS_REJEU[etape]})
        total_blocs = sum(len(s) for s in e["formats"].values())
        bilan["etapes"] += 1
        bilan["combats_c_prevus"] += 2 * total_blocs
        arret = None
        joues = total_blocs
        for p in paliers:
            par_format = {}
            for f, n in p.blocs.items():
                par_adv: dict[str, list[float]] = {}
                for x in e["formats"][f][:n]:
                    par_adv.setdefault(x["adversaire"], []).append(x["d"])
                par_format[f] = [st.composante(f, a, ds) for a, ds in par_adv.items()]
            est_final = p.rang == paliers[-1].rang
            if est_final and etape == "confirmation":
                break
            if est_final:
                break
            jug = prog.juger_palier(par_format, p, paliers, cfg, confirmation=(etape == "confirmation"))
            if jug.arret:
                arret = (p.rang, jug.arret, jug.raisons)
                joues = sum(p.blocs.values())
                break
        if arret:
            bilan["arrets"] += 1
            bilan["combats_c_evites"] += 2 * (total_blocs - joues)
            suite = [k for k in ("s2", "s3", "confirmation") if k in atteint[e["candidat"]]
                     and ["s1", "s2", "s3", "confirmation"].index(k) > ["s1", "s2", "s3", "confirmation"].index(etape)]
            conf = atteint[e["candidat"]].get("confirmation")
            a_tort = bool(suite) and (conf is None or conf["objectif"] > 0)
            ligne = {"candidat": e["candidat"], "etape": etape, "palier": arret[0], "arret": arret[1],
                     "raisons": arret[2], "objectif_historique": e["objectif"],
                     "etapes_suivantes_historiques": suite}
            bilan["detail"].append(ligne)
            if suite:
                bilan["arrets_a_tort"].append(ligne)
    if sortie:
        print("etapes rejouees %d, arrets %d, combats de candidat evites %d sur %d (%.1f %%)"
              % (bilan["etapes"], bilan["arrets"], bilan["combats_c_evites"], bilan["combats_c_prevus"],
                 100.0 * bilan["combats_c_evites"] / max(1, bilan["combats_c_prevus"])))
        for l in bilan["detail"]:
            print("  %-46s %-12s palier %d %-18s obj hist %+.3f suite %s | %s"
                  % (l["candidat"], l["etape"], l["palier"], l["arret"], l["objectif_historique"],
                     l["etapes_suivantes_historiques"], "; ".join(l["raisons"])[:120]))
    return bilan


if __name__ == "__main__":
    commande = sys.argv[1] if len(sys.argv) > 1 else "extraire"
    if commande == "extraire":
        extraire()
    elif commande == "variances":
        variances()
    elif commande == "simuler":
        base = {"variance_a_priori": {"farmer": 0.0825, "solo": 0.0352, "team": 0.099},
                "ddl_a_priori": 4, "seuil_objectif": 0.0}
        for nom, extra in (("alphas 0,05 / premier palier -0,10", {"alpha_plancher": 0.05, "alpha_futilite": 0.05, "alpha_objectif": 0.05, "seuil_premier_palier_farmer": -0.10}),
                           ("alphas 0,10 / premier palier -0,10", {"alpha_plancher": 0.10, "alpha_futilite": 0.10, "alpha_objectif": 0.10, "seuil_premier_palier_farmer": -0.10}),
                           ("alphas 0,20 / premier palier -0,05", {"alpha_plancher": 0.20, "alpha_futilite": 0.20, "alpha_objectif": 0.20, "seuil_premier_palier_farmer": -0.05}),
                           ("alphas 0,30 / sans seuil tolerant", {"alpha_plancher": 0.30, "alpha_futilite": 0.30, "alpha_objectif": 0.30})):
            print("==", nom)
            simuler(dict(base, **extra))
    else:
        raise SystemExit("commande inconnue : %s" % commande)

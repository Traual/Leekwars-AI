"""Ce qu'un net projete coute REELLEMENT a la cellule de fin de tour retenue.

`ScoringClass.exposureLoss(net) = EXPOSURE_FACTOR * COEF_LIFE * net`. La quantite comparable
n'est PAS les degats encaisses : `DeltaClass.netDamage` deroule l'ordre de tour en retranchant
les degats ET en ajoutant les soins et la vitalite projetes des allies, plafonne a la vie max,
puis rend `max(0, vie_avant - vie_apres)`. La sonde mesure donc la **variation nette de vie**, et
rend a part les degats directs, les flux periodiques et les soins recus, qui ne sont pas la meme
chose.

Appariement : le journal du moteur attache chaque ligne de debug a un INDICE D'ACTION. Une ligne
« Best final cell » est donc rattachee a la fenetre de tour qui contient cet indice, jamais au
n-ieme tour d'une entite. C'est indispensable : le premier champ d'une ligne de log porte
l'identifiant de la VM, donc **un bulbe journalise sous son invocateur**, et un placement manquant
decalerait tous les suivants.

Deux censures sont annoncees separement, jamais fondues dans le rapport :
  - une entite qui MEURT avant son tour suivant perd au plus la vie qui lui restait, alors que le
    net projete, lui, compte l'overkill ;
  - un placement dont la fenetre est tronquee par la fin du combat est ecarte.

Usage : python exposition.py <travail> [<travail2> ...] [--detail]
        (des repertoires produits par `decision.py combats`)
"""
from __future__ import annotations

import bisect
import collections
import json
import re
import statistics
import sys
from pathlib import Path

LIGNE = re.compile(r"Best final cell: (\d+) \(p.nalit.: ([-\d.E]+), net: ([-\d.E]+)\)")
A_LEEK_TURN, A_MORT, A_RESURRECT = 7, 5, 105
A_DIRECTS = (101, 108, 109)          # perte de vie, renvoi, degats de vie
A_FLUX = (110, 111)                  # poison, contrecoup
A_HEAL, A_VITALITE, A_NOVA_VIE, A_NOVA_DEGAT = 103, 104, 112, 107


def fenetres(fight: dict) -> list[dict]:
    """Une fenetre par tour d'entite : [debut, fin[ en indices d'action, et l'entite ACTIVE."""
    sortie = []
    for i, act in enumerate(fight.get("actions") or []):
        if act and act[0] == A_LEEK_TURN and len(act) > 1:
            if sortie:
                sortie[-1]["fin"] = i
            sortie.append({"debut": i, "fin": len(fight.get("actions") or []), "entite": act[1]})
    return sortie


def vies(fight: dict) -> list[dict[int, int]]:
    """Vie de chaque entite APRES chaque action, reconstruite comme le fait le harnais."""
    vie = {e["id"]: e["life"] for e in fight.get("leeks") or []}
    maxi = dict(vie)
    suite = []
    for act in fight.get("actions") or []:
        if act:
            t = act[0]
            if t == A_MORT and len(act) >= 2:
                vie[act[1]] = 0
            elif t in A_DIRECTS + A_FLUX and len(act) >= 4:
                vie[act[1]] = max(0, vie.get(act[1], 0) - act[2])
                maxi[act[1]] = max(1, maxi.get(act[1], 1) - act[3])
            elif t in A_DIRECTS + A_FLUX and len(act) >= 3:
                vie[act[1]] = max(0, vie.get(act[1], 0) - act[2])
            elif t == A_NOVA_DEGAT and len(act) >= 3:
                maxi[act[1]] = max(1, maxi.get(act[1], 1) - act[2])
            elif t == A_HEAL and len(act) >= 3:
                vie[act[1]] = min(maxi.get(act[1], 1), vie.get(act[1], 0) + act[2])
            elif t == A_VITALITE and len(act) >= 3:
                maxi[act[1]] = maxi.get(act[1], 1) + act[2]
                vie[act[1]] = vie.get(act[1], 0) + act[2]
            elif t == A_NOVA_VIE and len(act) >= 3:
                maxi[act[1]] = maxi.get(act[1], 1) + act[2]
            elif t == A_RESURRECT and len(act) >= 6:
                vie[act[2]] = act[4]
                maxi[act[2]] = act[5]
        suite.append(dict(vie))
    return suite


def placements(brut: dict, notre_camp: int) -> list[dict]:
    fight = (brut.get("outcome") or {}).get("fight") or {}
    ents = {e["id"]: e for e in fight.get("leeks") or []}
    notre = {i for i, e in ents.items() if e.get("team") == notre_camp}
    fen = fenetres(fight)
    apres = vies(fight)
    n_actions = len(fight.get("actions") or [])
    debuts = [f["debut"] for f in fen]

    def fenetre_de(indice: int) -> int | None:
        """La fenetre qui CONTIENT cet indice d'action, ou None avant le premier tour."""
        k = bisect.bisect_right(debuts, indice) - 1
        return k if k >= 0 else None

    lignes = []
    for _vm, par_indice in ((brut.get("outcome") or {}).get("logs") or {}).items():
        for indice, journal in par_indice.items():
            for l in journal:
                if len(l) >= 3 and isinstance(l[2], str):
                    m = LIGNE.search(l[2])
                    if m:
                        lignes.append((int(indice), float(m.group(3)), l[0]))
    lignes.sort()

    sortie = []
    for indice, net, vm in lignes:
        k = fenetre_de(indice)
        if k is None:
            continue
        e = fen[k]["entite"]
        # Fenetre d'exposition : de ce placement au DEBUT du tour suivant de la MEME entite.
        suivant = None
        for j in range(k + 1, len(fen)):
            if fen[j]["entite"] == e:
                suivant = fen[j]["debut"]
                break
        tronquee = suivant is None
        fin = n_actions - 1 if tronquee else suivant
        vie_avant = apres[indice].get(e, 0)
        vie_apres = apres[fin].get(e, 0)
        cumul = collections.Counter()
        mort = False
        for act in (fight.get("actions") or [])[indice + 1:fin + 1]:
            if not act or len(act) < 2 or act[1] != e:
                if act and act[0] == A_RESURRECT and len(act) >= 3 and act[2] == e:
                    cumul["resurrections"] += 1
                continue
            t = act[0]
            if t in A_DIRECTS and len(act) >= 3:
                cumul["directs"] += act[2]
            elif t in A_FLUX and len(act) >= 3:
                cumul["flux"] += act[2]
            elif t == A_HEAL and len(act) >= 3:
                cumul["soins"] += act[2]
            elif t == A_VITALITE and len(act) >= 3:
                cumul["vitalite"] += act[2]
            elif t == A_MORT:
                mort = True
        sortie.append({
            "camp": "nous" if e in notre else "reference",
            "entite": e, "invocation": bool(ents.get(e, {}).get("summon")),
            "vm": vm, "net": net, "perte_nette": max(0, vie_avant - vie_apres),
            "directs": cumul["directs"], "flux": cumul["flux"],
            "soins": cumul["soins"], "vitalite": cumul["vitalite"],
            "mort": mort, "tronquee": tronquee,
        })
    return sortie


def rapport(lot: list[dict], titre: str) -> None:
    if not lot:
        return
    sn = sum(p["net"] for p in lot)
    print("%-22s %5d | net %10.0f | perte nette %8d -> %.4f | directs %8d | soins+vita %8d | flux %6d"
          % (titre, len(lot), sn, sum(p["perte_nette"] for p in lot),
             sum(p["perte_nette"] for p in lot) / max(1.0, sn),
             sum(p["directs"] for p in lot), sum(p["soins"] + p["vitalite"] for p in lot),
             sum(p["flux"] for p in lot)))


def main() -> int:
    tout = []
    for travail in [a for a in sys.argv[1:] if not a.startswith("--")]:
        t = Path(travail).resolve()
        index = json.loads((t / "index.json").read_text(encoding="utf-8"))
        for combat in index["combats"]:
            brut = json.loads((t / combat["fichier"]).read_text(encoding="utf-8"))
            tout += placements(brut, combat["notre_camp"])
    if not tout:
        print("aucun placement")
        return 1
    melanges = sum(1 for p in tout if p["vm"] != p["entite"])
    print("%d placements | %d journalises sous une AUTRE entite que celle qui joue (%.0f %%)"
          % (len(tout), melanges, 100 * melanges / len(tout)))
    print("ecartes : %d fenetres tronquees par la fin du combat" % sum(1 for p in tout if p["tronquee"]))
    utiles = [p for p in tout if not p["tronquee"] and p["net"] > 0]
    print("%d placements a net > 0 et fenetre complete" % len(utiles))
    for camp in ("nous", "reference"):
        c = [p for p in utiles if p["camp"] == camp]
        for nom, lot in ((camp + " poireaux survivants", [p for p in c if not p["invocation"] and not p["mort"]]),
                         (camp + " poireaux morts", [p for p in c if not p["invocation"] and p["mort"]]),
                         (camp + " invocations", [p for p in c if p["invocation"]])):
            rapport(lot, nom)
    vivants = [p for p in utiles if not p["invocation"] and not p["mort"]]
    if vivants:
        print("poireaux survivants : net median %.0f | perte nette mediane %.0f | %d placements sans perte sur %d"
              % (statistics.median([p["net"] for p in vivants]),
                 statistics.median([p["perte_nette"] for p in vivants]),
                 sum(1 for p in vivants if p["perte_nette"] == 0), len(vivants)))
    if "--detail" in sys.argv:
        Path("exposition-detail.json").write_text(json.dumps(tout, indent=1), encoding="utf-8")
        print("detail -> exposition-detail.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

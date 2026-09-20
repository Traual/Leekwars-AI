"""Calibration de la penalite d'exposition : ce qu'un net projete coute REELLEMENT.

`ScoringClass.exposureLoss(net) = EXPOSURE_FACTOR * COEF_LIFE * net` pretend rendre les PV que
le net projete a la cellule de fin de tour coutera a son porteur. Cette sonde mesure le rapport
sur la population EXACTE ou la constante s'applique : la cellule que le placement a retenue.

Elle ne demande aucune instrumentation — `FinalCell.get` journalise deja « Best final cell: C
(penalite: P, net: N) » a chaque fin de tour. Le net est apparie au tour d'entite de meme rang
dans le journal d'actions, et la perte est la vie retiree a cette entite entre la fin de son
tour et son tour suivant.

Les tours ou l'entite MEURT avant son tour suivant sont comptes a part : leur perte est bornee
par la vie restante, et l'overkill n'y figure pas.

Usage : python exposition.py <travail> [<travail2> ...]
        (des repertoires produits par `decision.py combats`)
"""
from __future__ import annotations

import collections
import json
import re
import statistics
import sys
from pathlib import Path

# Degats DIRECTS : le net projette des attaques, pas les flux periodiques.
DEGATS_DIRECTS = (101, 108, 109)
LIGNE = re.compile(r"Best final cell: (\d+) \(p.nalit.: ([-\d.E]+), net: ([-\d.E]+)\)")


def paires(brut: dict, notre_camp: int) -> list[tuple[str, float, int, bool]]:
    """[(camp, net retenu, perte reelle jusqu'au tour suivant, mort)] pour chaque poireau."""
    fight = (brut.get("outcome") or {}).get("fight") or {}
    ents = {e["id"]: e for e in fight.get("leeks") or []}
    notre = {i for i, e in ents.items() if e.get("team") == notre_camp}
    leeks = {i for i, e in ents.items() if not e.get("summon")}
    nets = collections.defaultdict(list)
    for _f, par in ((brut.get("outcome") or {}).get("logs") or {}).items():
        for _a, journal in par.items():
            for l in journal:
                if len(l) >= 3 and isinstance(l[2], str):
                    m = LIGNE.search(l[2])
                    if m:
                        nets[l[0]].append(float(m.group(3)))
    seq = []
    for act in fight.get("actions") or []:
        if not act:
            continue
        if act[0] == 7:
            seq.append({"e": act[1], "perte": collections.Counter(), "morts": set()})
        elif seq and act[0] in DEGATS_DIRECTS and len(act) >= 3:
            seq[-1]["perte"][act[1]] += act[2]
        elif seq and act[0] == 5 and len(act) >= 2:
            seq[-1]["morts"].add(act[1])
    sorties, rang = [], collections.Counter()
    for k, t in enumerate(seq):
        e = t["e"]
        if e not in leeks:
            continue
        i = rang[e]
        rang[e] += 1
        if i >= len(nets[e]):
            continue
        net = nets[e][i]
        if net <= 0:
            continue
        perdu, mort = 0, False
        for suite in seq[k + 1:]:
            if suite["e"] == e:
                break
            perdu += suite["perte"].get(e, 0)
            if e in suite["morts"]:
                mort = True
                break
        sorties.append(("nous" if e in notre else "reference", net, perdu, mort))
    return sorties


def main() -> int:
    tout = []
    for travail in sys.argv[1:]:
        t = Path(travail).resolve()
        index = json.loads((t / "index.json").read_text(encoding="utf-8"))
        for combat in index["combats"]:
            brut = json.loads((t / combat["fichier"]).read_text(encoding="utf-8"))
            tout += paires(brut, combat["notre_camp"])
    if not tout:
        print("aucun placement a net non nul")
        return 1
    print("%-10s %-10s %6s %11s %11s %8s" % ("camp", "population", "n", "net total", "perte", "rapport"))
    for camp in ("nous", "reference"):
        for nom, lot in (("survit", [p for p in tout if p[0] == camp and not p[3]]),
                         ("meurt", [p for p in tout if p[0] == camp and p[3]]),
                         ("ensemble", [p for p in tout if p[0] == camp])):
            if not lot:
                continue
            sn = sum(p[1] for p in lot)
            sp = sum(p[2] for p in lot)
            print("%-10s %-10s %6d %11.0f %11d %8.4f" % (camp, nom, len(lot), sn, sp, sp / max(1.0, sn)))
    nous = [p for p in tout if p[0] == "nous"]
    print("net median %.0f | perte mediane %.0f | placements a perte nulle %d sur %d"
          % (statistics.median([p[1] for p in nous]), statistics.median([p[2] for p in nous]),
             sum(1 for p in nous if p[2] == 0), len(nous)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

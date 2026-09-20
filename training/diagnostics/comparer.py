"""Comparaison APPARIEE de lots joues sur les memes blocs.

L'unite de comparaison est le BLOC, pas l'orientation : chaque bloc est joue a gauche et a
droite, et son score est la moyenne des deux. C'est l'appariement qui annule le biais de
position ; compter chaque orientation comme une observation independante le reintroduirait.

Les lots compares doivent porter la meme graine, le meme nombre de blocs et la meme reference —
le script le verifie et refuse sinon.

Ce tableau sert au TRI. Huit combats ne departagent rien : aucune borne, aucune significativite.

Usage : python comparer.py <lot de reference> <lot> [<lot> ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def lire(travail: Path) -> dict:
    index = json.loads((travail / "index.json").read_text(encoding="utf-8"))
    blocs: dict[int, dict] = {}
    avortes = {"nous": 0, "reference": 0}
    ops = {"nous": [], "reference": []}
    for combat in index["combats"]:
        indice, cote = combat["etiquette"].rsplit("-", 1)
        indice = int(indice)
        # winner vaut 0 pour le camp de gauche, 1 pour celui de droite, -1 pour un nul.
        v = combat["vainqueur"]
        if v is None:
            score = None
        elif v == -1:
            score = 0.5
        else:
            score = 1.0 if (v == 0) == (combat["notre_camp"] == 1) else 0.0
        blocs.setdefault(indice, {})[cote] = score
        brut = json.loads((travail / combat["fichier"]).read_text(encoding="utf-8"))
        fight = (brut.get("outcome") or {}).get("fight") or {}
        ents = {e["id"]: e for e in fight.get("leeks") or []}
        duree = max(1, (brut.get("outcome") or {}).get("duration") or 1)
        for e, o in (fight.get("ops") or {}).items():
            e = int(e)
            if e not in ents or ents[e].get("summon"):
                continue
            cle = "nous" if ents[e].get("team") == combat["notre_camp"] else "reference"
            ops[cle].append(o / duree)
        # Un tour avorte est une erreur 1002 du journal d'actions ; on l'attribue par entite.
        for act in fight.get("actions") or []:
            if act and act[0] == 1002 and len(act) > 1:
                cle = "nous" if ents.get(act[1], {}).get("team") == combat["notre_camp"] else "reference"
                avortes[cle] += 1
    par_bloc = {}
    for i, cotes in blocs.items():
        vals = [v for v in cotes.values() if v is not None]
        par_bloc[i] = sum(vals) / len(vals) if vals else None
    return {"index": index, "blocs": par_bloc, "avortes": avortes,
            "ops": {k: (sum(v) / len(v) if v else 0) for k, v in ops.items()},
            "tours": sum(c["tours"] or 0 for c in index["combats"]) / len(index["combats"])}


def divergences(base: Path, autre: Path) -> None:
    """Premiere action ou les deux lots divergent, scenario par scenario, et le tour d'entite
    qui la contient : c'est la que la variante a change une decision, pas ailleurs."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import decision as dec
    index = json.loads((base / "index.json").read_text(encoding="utf-8"))
    for combat in index["combats"]:
        a = json.loads((base / combat["fichier"]).read_text(encoding="utf-8"))
        b = json.loads((autre / combat["fichier"]).read_text(encoding="utf-8"))
        aa, bb = dec.actions(a), dec.actions(b)
        ecart = None
        for i in range(min(len(aa), len(bb))):
            if aa[i] != bb[i]:
                ecart = i
                break
        if ecart is None and len(aa) != len(bb):
            ecart = min(len(aa), len(bb))
        if ecart is None:
            print("%-10s identique (%d actions)" % (combat["etiquette"], len(aa)))
            continue
        fen = [f for f in dec.fenetres(a) if f["debut"] <= ecart < f["fin"]]
        ents = dec.entites(a)
        if fen:
            f = fen[0]
            nom = ents.get(f["entite"], {}).get("name")
            notre = ents.get(f["entite"], {}).get("team") == combat["notre_camp"]
            print("%-10s 1er ecart action %5d | tour %2d | entite %2d %-16s %s"
                  % (combat["etiquette"], ecart, f["tour"], f["entite"], nom,
                     "(NOUS)" if notre else "(reference)"))
        else:
            print("%-10s 1er ecart action %5d (hors tour)" % (combat["etiquette"], ecart))


def main() -> int:
    lots = [Path(a).resolve() for a in sys.argv[1:] if not a.startswith("--")]
    if "--divergences" in sys.argv and len(lots) == 2:
        divergences(lots[0], lots[1])
        return 0
    if len(lots) < 2:
        print(__doc__)
        return 1
    donnees = [lire(l) for l in lots]
    base = donnees[0]
    for d, l in zip(donnees[1:], lots[1:]):
        for champ in ("graine", "reference"):
            if d["index"][champ] != base["index"][champ]:
                print("REFUS : %s a %s = %s, la reference a %s"
                      % (l.name, champ, d["index"][champ], base["index"][champ]))
                return 2
        if set(d["blocs"]) != set(base["blocs"]):
            print("REFUS : %s ne porte pas les memes blocs" % l.name)
            return 2
    indices = sorted(base["blocs"])
    print("graine %d | reference %s | %d blocs, chacun joue dans les deux orientations"
          % (base["index"]["graine"], base["index"]["reference"], len(indices)))
    entete = "%-16s" % "lot" + "".join("  bloc %d" % i for i in indices)
    print(entete + "   moyenne   ecart apparie   avortes n/ref   ops/poireau/tour   rapport")
    for d, l in zip(donnees, lots):
        vals = [d["blocs"][i] for i in indices]
        moy = sum(vals) / len(vals)
        if d is base:
            ecart = "        —"
        else:
            deltas = [d["blocs"][i] - base["blocs"][i] for i in indices]
            ecart = "%+.3f" % (sum(deltas) / len(deltas))
        print("%-16s" % l.name.replace("lot-", "")
              + "".join("  %6.2f" % v for v in vals)
              + "    %6.3f   %13s   %5d / %-5d   %16.0f   %.3f"
              % (moy, ecart, d["avortes"]["nous"], d["avortes"]["reference"],
                 d["ops"]["nous"], d["ops"]["nous"] / max(1.0, d["ops"]["reference"])))
    print()
    for d, l in zip(donnees[1:], lots[1:]):
        deltas = [(i, d["blocs"][i] - base["blocs"][i]) for i in indices]
        bouges = [(i, x) for i, x in deltas if x != 0]
        print("%-16s blocs deplaces : %s"
              % (l.name.replace("lot-", ""),
                 ", ".join("%d %+.2f" % (i, x) for i, x in bouges) if bouges else "aucun"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

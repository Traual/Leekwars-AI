"""Comparaison APPARIEE de lots joues sur les memes blocs.

L'unite de comparaison est le BLOC, pas l'orientation : chaque bloc est joue a gauche et a
droite, et son score est la moyenne des deux. C'est l'appariement qui annule le biais de
position ; compter chaque orientation comme une observation independante le reintroduirait.

Les lots compares doivent porter la meme graine, la meme reference, les memes blocs ET les memes
scenarios reellement joues. Chaque bloc doit porter EXACTEMENT une orientation gauche et une
droite, chacune avec un resultat valide : un doublon ou une orientation manquante rendrait une
moyenne de bloc qui n'est plus appariee, donc plus comparable. Le script refuse dans tous ces
cas au lieu de rendre un tableau muet.

Ce tableau sert au TRI. Huit combats ne departagent rien : aucune borne, aucune significativite.

Usage : python comparer.py <lot de reference> <lot> [<lot> ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ORIENTATIONS = ("gauche", "droite")


class Refus(Exception):
    """Un controle d'appariement a echoue : on ne rend aucun chiffre."""


def scenarios_par_role(travail: Path, index: dict) -> dict[str, dict]:
    """Les scenarios REELLEMENT joues, chemins d'IA remplaces par leur ROLE.

    Deux lots ne sont comparables que s'ils ont joue les memes compositions, les memes graines et
    les memes cœurs ; seuls les bundles doivent differer. Normaliser par role rend ces scenarios
    directement comparables d'un lot a l'autre.
    """
    sortie = {}
    for combat in index["combats"]:
        source = json.loads((travail / combat["scenario"]).read_text(encoding="utf-8"))
        for groupe in source.get("entities", []):
            for e in groupe:
                if e.get("ai") == index["nous"]:
                    e["ai"] = "ROLE_NOUS"
                elif e.get("ai") == index["eux"]:
                    e["ai"] = "ROLE_REFERENCE"
                else:
                    raise Refus("%s : %s porte une IA inconnue (%s)"
                                % (travail.name, combat["scenario"], e.get("ai")))
        sortie[combat["etiquette"]] = source
    return sortie


def tours_joues(fight: dict) -> dict[int, int]:
    """Tours REELLEMENT joues par chaque entite, comptes sur les actions de debut de tour
    (LEEK_TURN). La duree du combat ne les donne pas : une entite morte en cours de route,
    invoquee plus tard ou dont un tour est avorte n'en joue pas autant que le combat dure."""
    compte: dict[int, int] = {}
    for act in fight.get("actions") or []:
        if act and act[0] == 7 and len(act) > 1:
            compte[act[1]] = compte.get(act[1], 0) + 1
    return compte


def lire(travail: Path) -> dict:
    index = json.loads((travail / "index.json").read_text(encoding="utf-8"))
    blocs: dict[int, dict] = {}
    avortes = {"nous": 0, "reference": 0}
    ops = {"nous": [], "reference": []}
    for combat in index["combats"]:
        indice, cote = combat["etiquette"].rsplit("-", 1)
        indice = int(indice)
        if cote not in ORIENTATIONS:
            raise Refus("%s : orientation inconnue « %s »" % (travail.name, cote))
        if cote in blocs.get(indice, {}):
            raise Refus("%s : le bloc %d porte DEUX fois l'orientation « %s »"
                        % (travail.name, indice, cote))
        # winner vaut 0 pour le camp de gauche, 1 pour celui de droite, -1 pour un nul. Toute
        # autre valeur est un resultat que le moteur n'a pas tranche : la traiter comme une
        # defaite ou une victoire inventerait un demi-point.
        v = combat["vainqueur"]
        if v not in (-1, 0, 1):
            raise Refus("%s : le combat %s a un vainqueur inexploitable (%r)"
                        % (travail.name, combat["etiquette"], v))
        score = 0.5 if v == -1 else (1.0 if (v == 0) == (combat["notre_camp"] == 1) else 0.0)
        blocs.setdefault(indice, {})[cote] = score
        brut = json.loads((travail / combat["fichier"]).read_text(encoding="utf-8"))
        fight = (brut.get("outcome") or {}).get("fight") or {}
        ents = {e["id"]: e for e in fight.get("leeks") or []}
        joues = tours_joues(fight)
        for e, o in (fight.get("ops") or {}).items():
            e = int(e)
            if e not in ents or ents[e].get("summon") or joues.get(e, 0) == 0:
                continue
            # Le compteur d'operations est celui de la VM : le poireau ET ses invocations. On le
            # ramene aux tours que le POIREAU a joues, jamais a la duree du combat.
            cle = "nous" if ents[e].get("team") == combat["notre_camp"] else "reference"
            ops[cle].append(o / joues[e])
        # Un tour avorte est une erreur 1002 du journal d'actions ; on l'attribue par entite.
        for act in fight.get("actions") or []:
            if act and act[0] == 1002 and len(act) > 1:
                cle = "nous" if ents.get(act[1], {}).get("team") == combat["notre_camp"] else "reference"
                avortes[cle] += 1
    for i, cotes in blocs.items():
        manquantes = [c for c in ORIENTATIONS if c not in cotes]
        if manquantes:
            raise Refus("%s : le bloc %d n'a pas d'orientation %s"
                        % (travail.name, i, " ni ".join(manquantes)))
    par_bloc = {i: sum(cotes.values()) / len(ORIENTATIONS) for i, cotes in blocs.items()}
    return {"index": index, "blocs": par_bloc, "avortes": avortes,
            "scenarios": scenarios_par_role(travail, index),
            "ops": {k: (sum(v) / len(v) if v else 0) for k, v in ops.items()}}


def verifier_appariement(base: dict, autre: dict, nom: str) -> None:
    """Deux lots ne sont apparies que s'ils portent la meme graine, la meme reference, les memes
    blocs et les memes scenarios reellement joues."""
    for champ in ("graine", "reference"):
        if autre["index"][champ] != base["index"][champ]:
            raise Refus("%s a %s = %s, la reference a %s"
                        % (nom, champ, autre["index"][champ], base["index"][champ]))
    if set(autre["blocs"]) != set(base["blocs"]):
        raise Refus("%s ne porte pas les memes blocs" % nom)
    if set(autre["scenarios"]) != set(base["scenarios"]):
        raise Refus("%s ne porte pas les memes combats" % nom)
    for etiquette, scenario in base["scenarios"].items():
        if autre["scenarios"][etiquette] != scenario:
            raise Refus("%s : le scenario joue de %s differe de celui de la reference"
                        % (nom, etiquette))


def divergences(base: Path, autre: Path) -> None:
    """Premiere action ou les deux lots divergent, scenario par scenario, et le tour d'entite
    qui la contient : c'est la que la variante a change une decision, pas ailleurs.

    Memes controles d'appariement que le tableau : comparer action par action deux combats qui
    n'ont pas joue le meme scenario ne dit rien du tout."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import decision as dec
    verifier_appariement(lire(base), lire(autre), autre.name)
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
    try:
        if "--divergences" in sys.argv and len(lots) == 2:
            divergences(lots[0], lots[1])
            return 0
        if len(lots) < 2:
            print(__doc__)
            return 1
        donnees = [lire(l) for l in lots]
        base = donnees[0]
        for d, l in zip(donnees[1:], lots[1:]):
            verifier_appariement(base, d, l.name)
    except Refus as refus:
        print("REFUS : %s" % refus)
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

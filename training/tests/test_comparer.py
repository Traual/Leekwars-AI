"""Reception des CONTROLES de `training/diagnostics/comparer.py`.

Deux choses seulement, celles dont un chiffre faux ne se voit pas a l'œil :

  1. l'appariement — exactement une orientation gauche et une droite par bloc, chacune avec un
     resultat, et les memes scenarios reellement joues d'un lot a l'autre ;
  2. les operations par tour REELLEMENT joue, comptees sur les actions de debut de tour et non
     sur la duree du combat.

Les lots sont fabriques a la main dans un repertoire temporaire : aucun combat n'est rejoue.

    python training/tests/test_comparer.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "training" / "diagnostics"))

import comparer  # noqa: E402

LEEK_TURN = 7
ECHECS: list[str] = []


def verifier(nom: str, condition: bool, detail: str = "") -> None:
    print("[%s] %s%s" % ("OK " if condition else "ECHEC", nom, (" — " + detail) if detail else ""))
    if not condition:
        ECHECS.append(nom)


def combat(entites: list[dict], actions: list, ops: dict[int, int], vainqueur: int) -> dict:
    return {"winner": vainqueur,
            "outcome": {"duration": 10, "fight": {"leeks": entites, "actions": actions,
                                                  "ops": {str(k): v for k, v in ops.items()}},
                        "logs": {}}}


def ecrire_lot(racine: Path, nom: str, combats: list[tuple[str, int, int]],
               ops: dict[int, int] | None = None, actions: list | None = None,
               graine: int = 1, reference: str = "REF", cellule_gauche: int = 10) -> Path:
    """Un lot minimal : `combats` est [(etiquette, notre_camp, vainqueur)]."""
    travail = racine / nom
    travail.mkdir(parents=True, exist_ok=True)
    entites = [{"id": 0, "team": 1, "summon": False, "life": 100},
               {"id": 1, "team": 2, "summon": False, "life": 100},
               {"id": 2, "team": 1, "summon": True, "life": 50}]
    index = {"graine": graine, "reference": reference, "nous": "IA_NOUS", "eux": "IA_EUX",
             "empreinte_bundle": "abc", "combats": []}
    for etiquette, camp, vainqueur in combats:
        fichier = "combat-%s.json" % etiquette
        scenario = "scenario-%s.json" % etiquette
        gauche, droite = ("IA_NOUS", "IA_EUX") if camp == 1 else ("IA_EUX", "IA_NOUS")
        (travail / scenario).write_text(json.dumps({
            "entities": [[{"id": 1, "ai": gauche, "cell": cellule_gauche}],
                         [{"id": 2, "ai": droite, "cell": 20}]]}), encoding="utf-8")
        (travail / fichier).write_text(json.dumps(combat(
            entites, actions if actions is not None else [], ops or {}, vainqueur)),
            encoding="utf-8")
        index["combats"].append({"etiquette": etiquette, "scenario": scenario, "fichier": fichier,
                                 "notre_camp": camp, "vainqueur": vainqueur, "tours": 10,
                                 "tours_avortes": 0})
    (travail / "index.json").write_text(json.dumps(index), encoding="utf-8")
    return travail


def refuse(travail: Path) -> str | None:
    try:
        comparer.lire(travail)
        return None
    except comparer.Refus as r:
        return str(r)


def main() -> int:
    racine = Path(tempfile.mkdtemp(prefix="test-comparer-"))

    # --- 1. appariement -----------------------------------------------------------------
    sain = ecrire_lot(racine, "lot-sain", [("0-gauche", 1, 0), ("0-droite", 2, 0)])
    lu = comparer.lire(sain)
    verifier("un bloc complet est lu", lu["blocs"] == {0: 0.5},
             "bloc 0 = %s (gagne a gauche, perdu a droite)" % lu["blocs"][0])

    manquant = ecrire_lot(racine, "lot-manquant", [("0-gauche", 1, 0)])
    verifier("orientation manquante refusee", "n'a pas d'orientation droite" in (refuse(manquant) or ""),
             refuse(manquant) or "ACCEPTE")

    doublon = ecrire_lot(racine, "lot-doublon",
                         [("0-gauche", 1, 0), ("0-gauche", 1, 1), ("0-droite", 2, 0)])
    verifier("orientation en double refusee", "DEUX fois" in (refuse(doublon) or ""),
             refuse(doublon) or "ACCEPTE")

    sans = ecrire_lot(racine, "lot-sans-resultat", [("0-gauche", 1, None), ("0-droite", 2, 0)])
    verifier("resultat absent refuse", "n'a pas de resultat" in (refuse(sans) or ""),
             refuse(sans) or "ACCEPTE")

    # Scenarios : memes roles, mais une cellule de depart differente -> lots non comparables.
    autre = ecrire_lot(racine, "lot-autre-scenario",
                       [("0-gauche", 1, 0), ("0-droite", 2, 0)], cellule_gauche=99)
    try:
        comparer.verifier_appariement(comparer.lire(sain), comparer.lire(autre), "lot-autre-scenario")
        detail, ok = "ACCEPTE", False
    except comparer.Refus as r:
        detail, ok = str(r), "scenario joue" in str(r)
    verifier("scenario different refuse", ok, detail)

    # Le meme scenario joue par des bundles differents reste comparable : seuls les roles comptent.
    jumeau = ecrire_lot(racine, "lot-jumeau", [("0-gauche", 1, 1), ("0-droite", 2, 1)])
    jumeau_index = json.loads((jumeau / "index.json").read_text(encoding="utf-8"))
    jumeau_index["nous"] = "IA_VARIANTE"
    for c in jumeau_index["combats"]:
        p = jumeau / c["scenario"]
        p.write_text(p.read_text(encoding="utf-8").replace("IA_NOUS", "IA_VARIANTE"), encoding="utf-8")
    (jumeau / "index.json").write_text(json.dumps(jumeau_index), encoding="utf-8")
    try:
        comparer.verifier_appariement(comparer.lire(sain), comparer.lire(jumeau), "lot-jumeau")
        ok, detail = True, "accepte malgre des bundles differents"
    except comparer.Refus as r:
        ok, detail = False, str(r)
    verifier("bundles differents, meme scenario : accepte", ok, detail)

    # --- 2. operations par tour joue ----------------------------------------------------
    # Le poireau 0 joue 4 tours, le poireau 1 en joue 2, l'invocation 2 en joue 5. Le combat
    # dure 10 tours : diviser par la duree donnerait 100 au lieu de 1000 et 250.
    actions = ([[LEEK_TURN, 0]] * 4) + ([[LEEK_TURN, 1]] * 2) + ([[LEEK_TURN, 2]] * 5)
    lot_ops = ecrire_lot(racine, "lot-ops", [("0-gauche", 1, 0), ("0-droite", 2, 0)],
                         ops={0: 4000, 1: 500, 2: 9999}, actions=actions)
    lu = comparer.lire(lot_ops)
    compte = comparer.tours_joues({"actions": actions})
    verifier("tours comptes sur les debuts de tour", compte == {0: 4, 1: 2, 2: 5}, str(compte))
    # Les deux orientations echangent les camps : chaque camp recoit donc une fois 4000/4 = 1000
    # et une fois 500/2 = 250, soit 625. Par la DUREE (10 tours) ce serait 400 et 50, soit 225 :
    # c'est cet ecart que le controle attrape.
    verifier("operations par tour JOUE, pas par duree",
             lu["ops"]["nous"] == 625.0 and lu["ops"]["reference"] == 625.0,
             "nous %.1f / reference %.1f, attendu 625 ; par duree ce serait 225"
             % (lu["ops"]["nous"], lu["ops"]["reference"]))
    verifier("les invocations n'entrent pas dans le compte",
             9999 / 5 not in (lu["ops"]["nous"], lu["ops"]["reference"])
             and 9999 / 10 not in (lu["ops"]["nous"], lu["ops"]["reference"]))

    # Un poireau qui ne joue AUCUN tour est ecarte au lieu de diviser par zero : seul celui qui
    # a joue compte, des deux cotes.
    lot_zero = ecrire_lot(racine, "lot-zero", [("0-gauche", 1, 0), ("0-droite", 2, 0)],
                          ops={0: 4000, 1: 500}, actions=[[LEEK_TURN, 0]] * 4)
    lu = comparer.lire(lot_zero)
    verifier("poireau sans tour joue : ecarte, pas de division par zero",
             lu["ops"]["nous"] == 1000.0 and lu["ops"]["reference"] == 1000.0,
             "nous %.1f / reference %.1f, attendu 1000 des deux cotes (seul le poireau 0 a joue)"
             % (lu["ops"]["nous"], lu["ops"]["reference"]))

    print()
    if ECHECS:
        print("%d ECHEC(S) : %s" % (len(ECHECS), ", ".join(ECHECS)))
        return 1
    print("tous les controles passent")
    return 0


if __name__ == "__main__":
    sys.exit(main())

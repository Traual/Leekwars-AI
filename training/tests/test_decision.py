"""Reception de `decision.actions_jouees` : ce qu'une entite a REELLEMENT joue dans un tour.

Le piege est l'arme. `USE_WEAPON` ne porte pas l'arme tiree : elle vient du dernier
`SET_WEAPON`, qui ne porte pas d'entite non plus et qui persiste d'un tour a l'autre. Il faut
donc un balayage CHRONOLOGIQUE depuis le debut du combat, et decoder chaque tir avec l'arme
equipee a cet instant — pas avec celle de la fin du tour.

Les cas synthetiques utilisent une table de templates de test ; les cas reels lisent les lots
archives de `training/runs/diagnostics` et s'annoncent SAUTES quand ils sont absents (ce
repertoire est hors depot).

    python training/tests/test_decision.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "training" / "diagnostics"))

import decision as dec  # noqa: E402

NEW_TURN, LEEK_TURN, USE_CHIP, SET_WEAPON, USE_WEAPON, MOVE_TO, SUMMON = 6, 7, 12, 13, 16, 10, 9
LOTS = RACINE / "training" / "runs" / "diagnostics"
ECHECS: list[str] = []


def verifier(nom: str, condition: bool, detail: str = "") -> None:
    print("[%s] %s%s" % ("OK " if condition else "ECHEC", nom, (" — " + detail) if detail else ""))
    if not condition:
        ECHECS.append(nom)


def sauter(nom: str, pourquoi: str) -> None:
    print("[SAUT] %s — %s" % (nom, pourquoi))


def combat(actions: list, entites: list[dict] | None = None) -> dict:
    return {"outcome": {"fight": {"actions": actions,
                                  "leeks": entites or [{"id": 1, "team": 1, "summon": False,
                                                        "life": 100}]}}}


def table_de_test():
    """[template -> (nom, item)] pour les puces et pour les armes, volontairement CHEVAUCHANTES :
    le template 34 existe des deux cotes, comme dans les donnees du moteur."""
    puces = {34: ("adrenaline", 16), 94: ("serum", 168)}
    armes = {34: ("unstable_destroyer", 999), 25: ("lightninger", 180), 27: ("neutrino", 182)}
    return puces, armes


def avec_table_de_test(fn):
    vraie = dec.table_templates
    dec.table_templates = table_de_test
    try:
        return fn()
    finally:
        dec.table_templates = vraie


def noms(jouees, sorte=None):
    return [a["nom"] for a in jouees if sorte is None or a["sorte"] == sorte]


def main() -> int:
    # --- 1. deux armes successives DANS le meme tour ------------------------------------
    actes = [
        [LEEK_TURN, 1],            # 0 : tour 1 de l'entite 1
        [SET_WEAPON, 25],          # 1 : equipe lightninger
        [USE_WEAPON, 100, 1],      # 2 : tire AU lightninger
        [SET_WEAPON, 27],          # 3 : equipe neutrino
        [USE_WEAPON, 101, 1],      # 4 : tire AU neutrino
        [LEEK_TURN, 2],            # 5 : fin de la fenetre
    ]
    jouees = avec_table_de_test(lambda: dec.actions_jouees(combat(actes), 1, 1))
    tirs = [(a["indice"], a["nom"]) for a in jouees if a["sorte"] == "arme"]
    verifier("deux armes successives : chacune son tir", tirs == [(2, "lightninger"), (4, "neutrino")],
             str(tirs))

    # --- 2. arme CONSERVEE depuis le tour precedent -------------------------------------
    actes = [
        [LEEK_TURN, 1],            # 0 : tour 1
        [SET_WEAPON, 25],          # 1 : equipe lightninger
        [USE_WEAPON, 100, 1],      # 2
        [NEW_TURN, 2],             # 3
        [LEEK_TURN, 1],            # 4 : tour 2, AUCUN SET_WEAPON
        [USE_WEAPON, 102, 1],      # 5 : tire encore au lightninger
        [LEEK_TURN, 2],            # 6
    ]
    jouees = avec_table_de_test(lambda: dec.actions_jouees(combat(actes), 2, 1))
    verifier("arme conservee du tour precedent", noms(jouees, "arme") == ["lightninger"],
             str([(a["indice"], a["nom"]) for a in jouees]))
    verifier("seule la fenetre demandee est rendue",
             [a["indice"] for a in jouees] == [5],
             str([a["indice"] for a in jouees]))

    # --- 3. l'arme d'une AUTRE entite ne fuit pas ---------------------------------------
    actes = [
        [LEEK_TURN, 1],            # 0
        [SET_WEAPON, 25],          # 1 : l'entite 1 equipe lightninger
        [LEEK_TURN, 2],            # 2
        [SET_WEAPON, 27],          # 3 : l'entite 2 equipe neutrino
        [USE_WEAPON, 100, 1],      # 4
        [NEW_TURN, 2],             # 5
        [LEEK_TURN, 1],            # 6 : tour 2 de l'entite 1
        [USE_WEAPON, 101, 1],      # 7 : doit rester lightninger
        [LEEK_TURN, 2],            # 8
    ]
    jouees = avec_table_de_test(lambda: dec.actions_jouees(combat(actes), 2, 1))
    verifier("l'arme d'une autre entite ne fuit pas", noms(jouees, "arme") == ["lightninger"],
             str(noms(jouees, "arme")))

    # --- 4. puce et arme de MEME template sont decodees separement ----------------------
    actes = [
        [LEEK_TURN, 1],
        [SET_WEAPON, 34],          # 1 : arme de template 34
        [USE_CHIP, 34, 100, 1],    # 2 : puce de template 34
        [USE_WEAPON, 100, 1],      # 3 : arme de template 34
        [LEEK_TURN, 2],
    ]
    jouees = avec_table_de_test(lambda: dec.actions_jouees(combat(actes), 1, 1))
    verifier("template chevauchant : le type tranche",
             noms(jouees, "puce") == ["adrenaline"] and noms(jouees, "arme") == ["unstable_destroyer"],
             str([(a["sorte"], a["nom"]) for a in jouees]))

    # --- 5. cas REEL : deux armes dans le tour 3 de l'entite 4 --------------------------
    nom = "cas reel : quantum_rifle puis neutrino (d8-temoin 0-droite t3 e4)"
    fichier = LOTS / "d8-temoin" / "combat-0-droite.json"
    if not fichier.exists():
        sauter(nom, "lot archive absent (%s)" % fichier)
    else:
        brut = json.loads(fichier.read_text(encoding="utf-8"))
        jouees = dec.actions_jouees(brut, 3, 4)
        par_indice = {a["indice"]: (a["sorte"], a["nom"]) for a in jouees}
        verifier(nom,
                 par_indice.get(406) == ("arme", "quantum_rifle")
                 and par_indice.get(433) == ("arme equipee", "neutrino")
                 and par_indice.get(434) == ("arme", "neutrino"),
                 "406 %s | 433 %s | 434 %s" % (par_indice.get(406), par_indice.get(433),
                                               par_indice.get(434)))

    # --- 6. cas REEL : les comptes 4 / 4 / 7 avec Serum ---------------------------------
    attendus = [("d8-temoin", 4, False), ("d8-soin-bas", 4, False), ("d8-soin-haut", 7, True)]
    for lot, combien, avec_serum in attendus:
        nom = "cas reel : %s joue %d casts%s" % (lot, combien, " dont serum" if avec_serum else "")
        fichier = LOTS / lot / "combat-0-gauche.json"
        if not fichier.exists():
            sauter(nom, "lot archive absent")
            continue
        brut = json.loads(fichier.read_text(encoding="utf-8"))
        casts = [a["nom"] for a in dec.actions_jouees(brut, 1, 2) if a["sorte"] in ("puce", "arme")]
        verifier(nom, len(casts) == combien and (("serum" in casts) == avec_serum),
                 "%d casts : %s" % (len(casts), ", ".join(casts)))

    print()
    if ECHECS:
        print("%d ECHEC(S) : %s" % (len(ECHECS), ", ".join(ECHECS)))
        return 1
    print("tous les controles passent")
    return 0


if __name__ == "__main__":
    sys.exit(main())

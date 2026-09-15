"""Variantes de builds 3.00, nommees et versionnees, a partir du snapshot du meta.

Usage : python training/v3/variantes.py [--verifier]

Chaque famille remplace, sur deux eleveurs du snapshot, UNE puce par une puce 3.00 dans les deux
premiers poireaux qui portent la puce retiree ; ces deux poireaux recoivent les plus petits
identifiants de l'eleveur variante, donc ce sont eux que les formats solo et team retiennent.
Rien d'autre ne change : ni les caracteristiques, ni les composants, ni le nombre de puces — un
remplacement un pour un garde les emplacements legaux, et chaque puce ajoutee est de niveau
inferieur ou egal au poireau. Les quatre puces reservees aux plantes ne sont jamais equipees.

Le fichier produit contient TOUS les builds originaux, inchanges, puis les eleveurs variantes
sous des identifiants neufs : un eleveur variante est la copie de son eleveur source, deux
poireaux modifies et deux poireaux identiques. Les originaux gardent donc leur presence reelle
dans le tirage des blocs, et un bloc attache les memes builds aux memes creneaux dans ses deux
orientations, candidat ou reference.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ICI = Path(__file__).resolve().parent
TRAINING = ICI.parent
SOURCE = TRAINING / "data" / "meta_builds.jsonl"
SORTIE = TRAINING / "data" / "builds_v3.jsonl"
CATALOGUE = Path("C:/Users/aurel/Desktop/lw-gen-300/data/chips.json")

PUCES_DE_PLANTE = {"piquant", "capsaicin", "sugar", "popcorn"}

# famille -> (eleveurs source, [(puce retiree, puce ajoutee)], hypothese de jeu)
FAMILLES = {
    "poison-surinfection": {
        "version": 1,
        "eleveurs": [76772, 122557],
        "remplacements": {76772: [("fracture", "superinfection")],
                          122557: [("slow_down", "superinfection")]},
        "role": "poison avec Surinfection : convertir les lignes de poison deja posees",
    },
    "antisoin-hemorragie": {
        "version": 1,
        "eleveurs": [124210, 73979],
        "remplacements": {124210: [("flash", "hemorrhage")],
                          73979: [("wall", "hemorrhage")]},
        "role": "corps a corps avec Hemorragie : empecher les soins d'une cible",
    },
    "soutien-mais-maturation": {
        "version": 1,
        "eleveurs": [35602, 50078],
        "remplacements": {35602: [("seven_league_boots", "corn"), ("reflexes", "maturation")],
                          50078: [("seven_league_boots", "corn"), ("reflexes", "maturation")]},
        "role": "soutien a bulbes avec Mais et Maturation",
    },
    "pression-piment": {
        "version": 1,
        "eleveurs": [98186, 76155],
        "remplacements": {98186: [("motivation", "chilli_pepper")],
                          76155: [("wall", "chilli_pepper")]},
        "role": "pression avec Piment : une zone qui frappe ce qui y entre",
    },
    "geometrie-prototaxite": {
        "version": 1,
        "eleveurs": [91992, 76431],
        "remplacements": {91992: [("spark", "prototaxite")],
                          76431: [("spark", "prototaxite")]},
        "role": "tank avec Prototaxite : un corps qui bloque lignes de vue et chemins",
    },
    "trebuchet": {
        "version": 1,
        "eleveurs": [44975, 101978],
        "remplacements": {44975: [("burning", "trebuchet")],
                          101978: [("burning", "trebuchet")]},
        "role": "force avec Trebuchet : cercle 3 sans ligne de vue, recharge 5",
    },
}

ID_BUILD_BASE = 900000
ID_ELEVEUR_BASE = 900000


def charger():
    lignes = [json.loads(l) for l in SOURCE.read_text(encoding="utf-8").splitlines() if l.strip()]
    builds = [b for b in lignes if "stats" in b]
    return lignes, builds


def construire():
    catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    puce = {v["name"]: (int(k), v["level"]) for k, v in catalogue.items()}
    lignes, builds = charger()
    par_eleveur = {}
    for b in builds:
        par_eleveur.setdefault(b["farmer"]["id"], []).append(b)
    variantes = []
    n_build = 0
    n_eleveur = 0
    for famille, spec in FAMILLES.items():
        for source in spec["eleveurs"]:
            membres = sorted(par_eleveur[source], key=lambda b: b["id"])[:4]
            if len(membres) < 4:
                raise SystemExit("eleveur %d incomplet" % source)
            n_eleveur += 1
            eleveur = {"id": ID_ELEVEUR_BASE + n_eleveur,
                       "name": "%s-%s-v%d" % (membres[0]["farmer"]["name"], famille, spec["version"])}
            regles = spec["remplacements"][source]
            porteurs = [b for b in membres
                        if all(r in [c["name"] for c in b["chips"]] for r, _a in regles)][:2]
            if len(porteurs) < 2:
                raise SystemExit("%s : moins de deux poireaux de %d portent %s" % (famille, source, regles))
            ordre = porteurs + [b for b in membres if b not in porteurs]
            for b in ordre:
                n_build += 1
                v = json.loads(json.dumps(b))
                v["id"] = ID_BUILD_BASE + n_build
                v["farmer"] = eleveur
                faits = []
                if b in porteurs:
                    for retiree, ajoutee in regles:
                        noms = [c["name"] for c in v["chips"]]
                        if retiree not in noms:
                            raise SystemExit("%s : %s absente du build %d" % (famille, retiree, b["id"]))
                        if ajoutee in PUCES_DE_PLANTE:
                            raise SystemExit("puce de plante interdite sur un poireau : %s" % ajoutee)
                        if ajoutee in noms:
                            raise SystemExit("%s deja equipee sur %d" % (ajoutee, b["id"]))
                        ident, niveau = puce[ajoutee]
                        if niveau > b["level"]:
                            raise SystemExit("%s de niveau %d sur un poireau de niveau %d" % (ajoutee, niveau, b["level"]))
                        i = noms.index(retiree)
                        v["chips"][i] = {"template": ident, "name": ajoutee}
                        faits.append([retiree, ajoutee])
                v["name"] = b["name"] + ("-v" if faits else "-c")
                v["variante"] = {"famille": famille, "version": spec["version"],
                                 "build_source": b["id"], "eleveur_source": source,
                                 "remplacements": faits, "role": spec["role"]}
                v["source_url"] = b.get("source_url")
                variantes.append(v)
    manifeste = {"kind": "manifest_v3", "source": str(SOURCE.name),
                 "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                 "familles": {f: {"version": s["version"], "eleveurs_source": s["eleveurs"],
                                  "remplacements": {str(k): v for k, v in s["remplacements"].items()},
                                  "role": s["role"]} for f, s in FAMILLES.items()},
                 "originaux": len(builds), "variantes": len(variantes),
                 "_note": ("Originaux inchanges, puis eleveurs variantes : deux poireaux modifies "
                           "(identifiants les plus petits de l'eleveur) et deux copies. Stats, "
                           "composants et nombre de puces identiques a la source.")}
    sortie = [json.dumps(manifeste, ensure_ascii=False, separators=(",", ":"))]
    sortie += [json.dumps(l, ensure_ascii=False, separators=(",", ":")) for l in lignes]
    sortie += [json.dumps(v, ensure_ascii=False, separators=(",", ":")) for v in variantes]
    return "\n".join(sortie) + "\n", variantes


def main():
    texte, variantes = construire()
    if "--verifier" in sys.argv:
        if SORTIE.read_text(encoding="utf-8") != texte:
            raise SystemExit("builds_v3.jsonl ne correspond pas a la definition des variantes")
        print("verification OK")
        return
    SORTIE.write_text(texte, encoding="utf-8", newline="\n")
    for v in variantes:
        if v["variante"]["remplacements"]:
            print("%-26s %-24s %s" % (v["variante"]["famille"], v["name"], v["variante"]["remplacements"]))
    print("%d builds variantes ecrits dans %s" % (len(variantes), SORTIE))


if __name__ == "__main__":
    main()

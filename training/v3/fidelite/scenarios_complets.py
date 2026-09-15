"""Combats COMPLETS joues par l'IA reelle (Main.leek) avec les puces 3.00 : aucune sonde, on
verifie que tout se charge, que les plantes sont invoquees et se reveillent, et qu'aucune
erreur systeme n'apparait. Coeurs moderes, pour exercer aussi le budget."""

from scenarios_fidelite import c

BASE = {"life": 2600, "strength": 350, "wisdom": 200, "agility": 150, "resistance": 150,
        "science": 100, "magic": 200, "frequency": 100, "cores": 14, "ram": 30, "tp": 20, "mp": 6}


def stats(**k):
    s = dict(BASE)
    s.update(k)
    return s


COMPLETS = [
    {
        "nom": "complet_plantes_poison",
        "tours": 25,
        "graine": 4242,
        "entites": [
            {"nom": "P1", "camp": 1, "cellule": c(-6, -2), "ia": "main", "stats": stats(frequency=120),
             "armes": ["sun_spear"],
             "puces": ["chilli_pepper", "corn", "venom", "toxin", "superinfection", "cure", "shield"]},
            {"nom": "P2", "camp": 1, "cellule": c(-6, 2), "ia": "main", "stats": stats(frequency=90),
             "armes": ["desert_saber"],
             "puces": ["prototaxite", "maturation", "puny_bulb", "hemorrhage", "flame", "armoring"]},
            {"nom": "E1", "camp": 2, "cellule": c(6, -2), "ia": "main", "stats": stats(frequency=110),
             "armes": ["sun_spear"],
             "puces": ["chilli_pepper", "plague", "superinfection", "hemorrhage", "cure", "helmet"]},
            {"nom": "E2", "camp": 2, "cellule": c(6, 2), "ia": "main", "stats": stats(frequency=80),
             "armes": ["desert_saber"],
             "puces": ["corn", "trebuchet", "maturation", "rocky_bulb", "vaccine", "wall"]},
        ],
    },
    {
        "nom": "complet_solo_hemorragie",
        "tours": 30,
        "graine": 777,
        "entites": [
            {"nom": "S1", "camp": 1, "cellule": c(-5, 0), "ia": "main", "stats": stats(cores=10),
             "armes": ["sun_spear"],
             "puces": ["hemorrhage", "chilli_pepper", "flame", "cure", "shield", "prototaxite"]},
            {"nom": "S2", "camp": 2, "cellule": c(5, 0), "ia": "main", "stats": stats(cores=10),
             "armes": ["desert_saber"],
             "puces": ["corn", "regeneration", "venom", "superinfection", "vaccine", "helmet"]},
        ],
    },
]

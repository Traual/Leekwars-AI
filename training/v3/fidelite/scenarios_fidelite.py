"""Scenarios du banc de fidelite 3.00.

Une entite porte : nom, camp (1 ou 2), cellule (x, y), stats (surcharges), armes, puces, et
optionnellement un script {tour: [actions]}. Actions : ("chip", puce, (x, y)),
("weapon", arme, (x, y)), ("summon", puce, (x, y)), ("move", (x, y)).
La frequence fixe l'ordre de jeu : la plus haute joue d'abord.
"""

# Carte sans obstacle, losange x 0..34, y -17..17 ; centre (17, 0). La distance de cases est
# |dx| + |dy|. `c(dx, dy)` donne une cellule relative au centre.


def c(dx, dy):
    return (17 + dx, dy)

POISONS = ["venom", "toxin", "plague", "superinfection"]

SCENARIOS = [
    {
        # Temoin : poison et degats deja modelises, doit sortir sans ecart.
        "nom": "temoin_poison",
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"magic": 100, "strength": 100, "frequency": 200},
             "puces": ["venom", "toxin", "flame"],
             "script": {1: [("chip", "venom", c(2, 0)), ("chip", "toxin", c(2, 0)), ("chip", "flame", c(2, 0))]}},
            {"nom": "B", "camp": 2, "cellule": c(2, 0)},
        ],
    },
    {
        # Surinfection sur deux lignes de poison fraiches, sans critique.
        "nom": "surinf_base",
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"magic": 137, "frequency": 200},
             "puces": ["venom", "toxin", "superinfection"],
             "script": {1: [("chip", "venom", c(2, 0)), ("chip", "toxin", c(2, 0)),
                            ("chip", "superinfection", c(2, 0))]}},
            {"nom": "B", "camp": 2, "cellule": c(2, 0)},
        ],
    },
    {
        # Critique certain (agilite 1000) : poisons x1,3 et conversion a 65 %.
        "nom": "surinf_critique",
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"magic": 91, "agility": 1000, "frequency": 200},
             "puces": ["venom", "plague", "superinfection"],
             "script": {1: [("chip", "venom", c(2, 0)), ("chip", "plague", c(2, 0)),
                            ("chip", "superinfection", c(2, 0))]}},
            {"nom": "B", "camp": 2, "cellule": c(2, 0)},
        ],
    },
    {
        # Poison d'un tour sur l'autre : les durees ont tourne au tour du lanceur, et un
        # second lanceur (C) ajoute une ligne a lui.
        "nom": "surinf_tour2",
        "tours": 4,
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"magic": 53, "frequency": 200},
             "puces": ["venom", "superinfection"],
             "script": {1: [("chip", "venom", c(2, 0))], 2: [("chip", "superinfection", c(2, 0))]}},
            {"nom": "C", "camp": 1, "cellule": c(0, 2), "stats": {"magic": 77, "frequency": 50},
             "puces": ["toxin"],
             "script": {1: [("chip", "toxin", c(2, 0))]}},
            {"nom": "B", "camp": 2, "cellule": c(2, 0), "stats": {"frequency": 100}},
        ],
    },
    {
        # Surinfection mortelle : la cible meurt des degats convertis.
        "nom": "surinf_mort",
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"magic": 200, "frequency": 200},
             "puces": ["plague", "toxin", "superinfection"],
             "script": {1: [("chip", "plague", c(2, 0)), ("chip", "toxin", c(2, 0)),
                            ("chip", "superinfection", c(2, 0))]}},
            {"nom": "B", "camp": 2, "cellule": c(2, 0), "stats": {"life": 400}},
        ],
    },
    {
        # Cible invincible : les degats convertis tombent a zero, la reduction a lieu.
        "nom": "surinf_invincible",
        "entites": [
            {"nom": "B", "camp": 2, "cellule": c(2, 0), "stats": {"frequency": 300},
             "puces": ["divine_protection"],
             "script": {1: [("chip", "divine_protection", c(2, 0))]}},
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"magic": 100, "frequency": 200},
             "puces": ["venom", "toxin", "superinfection"],
             "script": {1: [("chip", "venom", c(2, 0)), ("chip", "toxin", c(2, 0)),
                            ("chip", "superinfection", c(2, 0))]}},
        ],
    },
]

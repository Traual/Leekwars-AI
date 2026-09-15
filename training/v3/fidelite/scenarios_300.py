"""Scenarios 3.00 du banc de fidelite : Hemorragie, vitalite, Maturation, plantes, reveils,
armes du catalogue. Memes conventions que scenarios_fidelite.py."""

from scenarios_fidelite import c

HEMORRAGIE = [
    {
        # A blesse B puis lui pose Hemorragie ; B tente un soin, une vitalite et un soin sur la
        # duree. Au tour 2, l'etat est tombe au debut du tour de A.
        "nom": "hemorragie_soins",
        "tours": 3,
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"strength": 300, "frequency": 200},
             "puces": ["flame", "hemorrhage"],
             "script": {1: [("chip", "flame", c(2, 0)), ("chip", "hemorrhage", c(2, 0))],
                        2: [("chip", "flame", c(2, 0))]}},
            {"nom": "B", "camp": 2, "cellule": c(2, 0), "stats": {"wisdom": 150, "frequency": 100},
             "puces": ["cure", "armoring", "vaccine", "bandage"],
             "script": {1: [("chip", "cure", c(2, 0)), ("chip", "armoring", c(2, 0)), ("chip", "vaccine", c(2, 0))],
                        2: [("chip", "bandage", c(2, 0))]}},
        ],
    },
    {
        # Maturation sur un bulbe allie : vie max et vie, puissance permanente irreductible.
        "nom": "maturation",
        "tours": 2,
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"wisdom": 213, "frequency": 200},
             "puces": ["puny_bulb", "maturation"],
             "script": {1: [("summon", "puny_bulb", c(0, 2)), ("chip", "maturation", c(0, 2))]}},
            {"nom": "B", "camp": 2, "cellule": c(8, 2)},
        ],
    },
    {
        # Maturation sur un bulbe rendu insoignable par l'ennemi : seule la vie max monte.
        "nom": "maturation_insoignable",
        "tours": 3,
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"wisdom": 137, "frequency": 200},
             "puces": ["rocky_bulb", "maturation"],
             "script": {1: [("summon", "rocky_bulb", c(2, 1))],
                        2: [("chip", "maturation", c(2, 1))]}},
            {"nom": "B", "camp": 2, "cellule": c(3, 2), "stats": {"strength": 200, "frequency": 100},
             "puces": ["flame", "hemorrhage"],
             "script": {1: [("chip", "flame", c(2, 1)), ("chip", "hemorrhage", c(2, 1))]}},
        ],
    },
    {
        # Surinfection sur un porteur de lance-grenades illicite : les degats de poison convertis
        # declenchent son passif de science pendant le lancer.
        "nom": "surinf_illicite",
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"magic": 180, "frequency": 200},
             "puces": ["plague", "toxin", "superinfection"],
             "script": {1: [("chip", "plague", c(2, 0)), ("chip", "toxin", c(2, 0)),
                            ("chip", "superinfection", c(2, 0))]}},
            {"nom": "B", "camp": 2, "cellule": c(2, 0), "armes": ["illicit_grenade_launcher"]},
        ],
    },
]

PUCES_PLANTES = ["corn", "chilli_pepper", "prototaxite"]

PLANTES = [
    {
        # Invocation des trois plantes pres de A : caracteristiques, Enracine, et reveil par A
        # deja dans la zone (plante posee).
        "nom": "plantes_invocation",
        "tours": 2,
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"frequency": 200},
             "puces": PUCES_PLANTES,
             "script": {1: [("summon", "corn", c(0, 2)), ("summon", "chilli_pepper", c(-2, 0)),
                            ("summon", "prototaxite", c(0, -2))]}},
            {"nom": "B", "camp": 2, "cellule": c(10, 0)},
        ],
    },
    {
        # Piment pose au contact de deux ennemis et de A : trois reveils d'affilee, dans l'ordre
        # des camps puis des ids.
        "nom": "plante_posee_ennemis",
        "tours": 2,
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"frequency": 200},
             "puces": ["chilli_pepper"],
             "script": {1: [("summon", "chilli_pepper", c(2, 0))]}},
            {"nom": "B", "camp": 2, "cellule": c(3, 1)},
            {"nom": "C", "camp": 2, "cellule": c(4, -1)},
        ],
    },
    {
        # B plante un Piment ; A traverse sa zone en marchant : reveil a la premiere case
        # d'entree, Piquants sur A, puis A reprend sa marche.
        "nom": "reveil_marche",
        "tours": 2,
        "entites": [
            {"nom": "B", "camp": 2, "cellule": c(0, 5), "stats": {"frequency": 300},
             "puces": ["chilli_pepper"],
             "script": {1: [("summon", "chilli_pepper", c(0, 2))]}},
            {"nom": "A", "camp": 1, "cellule": c(-4, 0), "stats": {"frequency": 100, "mp": 8},
             "script": {1: [("move", c(4, 0))]}},
        ],
    },
    {
        # A plante un Piment puis pousse B a travers sa zone : la glissade pose B a l'arrivee
        # AVANT le reveil, et la plante le voit hors de portee.
        "nom": "reveil_poussee_traversee",
        "tours": 2,
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"frequency": 200},
             "puces": ["chilli_pepper", "boxing_glove"],
             "script": {1: [("summon", "chilli_pepper", c(4, 3)), ("chip", "boxing_glove", c(7, 0))]}},
            {"nom": "B", "camp": 2, "cellule": c(3, 0)},
        ],
    },
    {
        # Meme poussee, arrivee DANS la zone : Piquants sur B.
        "nom": "reveil_poussee_arrivee",
        "tours": 2,
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"frequency": 200},
             "puces": ["chilli_pepper", "boxing_glove"],
             "script": {1: [("summon", "chilli_pepper", c(4, 3)), ("chip", "boxing_glove", c(4, 0))]}},
            {"nom": "B", "camp": 2, "cellule": c(3, 0)},
        ],
    },
    {
        # B plante un Piment ; A se teleporte dans sa zone au tour 2 (recharge initiale de la
        # Teleportation) : seule l'arrivee compte.
        "nom": "reveil_teleportation",
        "tours": 3,
        "entites": [
            {"nom": "B", "camp": 2, "cellule": c(0, 6), "stats": {"frequency": 300},
             "puces": ["chilli_pepper"],
             "script": {1: [("summon", "chilli_pepper", c(0, 3))]}},
            {"nom": "A", "camp": 1, "cellule": c(-6, 0), "stats": {"frequency": 100},
             "puces": ["teleportation"],
             "script": {2: [("chip", "teleportation", c(1, 1))]}},
        ],
    },
    {
        # B plante un Piment ; A invoque un bulbe dans sa zone : le bulbe le reveille.
        "nom": "reveil_invocation",
        "tours": 2,
        "entites": [
            {"nom": "B", "camp": 2, "cellule": c(0, 6), "stats": {"frequency": 300},
             "puces": ["chilli_pepper"],
             "script": {1: [("summon", "chilli_pepper", c(0, 3))]}},
            {"nom": "A", "camp": 1, "cellule": c(0, -1), "stats": {"frequency": 100},
             "puces": ["puny_bulb"],
             "script": {1: [("summon", "puny_bulb", c(0, 1))]}},
        ],
    },
    {
        # A plante un Mais, blesse C (allie) puis l'inverse dans la zone du Mais : A entre aussi
        # dans la zone, puis C ; le Mais soigne C.
        "nom": "reveil_inversion_mais",
        "tours": 3,
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"frequency": 200, "strength": 400},
             "puces": ["corn", "inversion", "flame"],
             "script": {1: [("summon", "corn", c(-3, 0)), ("chip", "flame", c(6, 0))],
                        2: [("chip", "inversion", c(6, 0))]}},
            {"nom": "C", "camp": 1, "cellule": c(6, 0), "stats": {"frequency": 50}},
            {"nom": "B", "camp": 2, "cellule": c(10, 5)},
        ],
    },
]

PROPRIETAIRE = [
    {
        # A marche dans la zone de SON Piment : la plante joue au milieu de l'action de A, dans
        # sa VM, puis A reprend la main et lance encore. La sonde de l'etape suivante prouve
        # que Me, ses etats et ses caches n'ont pas ete ecrases.
        "nom": "reveil_proprietaire",
        "tours": 2,
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"strength": 150, "frequency": 200},
             "puces": ["chilli_pepper", "flame"],
             "script": {1: [("summon", "chilli_pepper", c(0, 6)), ("move", c(0, 4)),
                            ("chip", "flame", c(2, 6))]}},
            {"nom": "B", "camp": 2, "cellule": c(2, 6)},
        ],
    },
]

ARMES = [
    {
        # Sabre du desert : degats et Sterile ; B ne peut plus invoquer.
        "nom": "sabre_sterile",
        "tours": 2,
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"strength": 200, "frequency": 200},
             "armes": ["desert_saber"],
             "script": {1: [("weapon", "desert_saber", c(1, 0))]}},
            {"nom": "B", "camp": 2, "cellule": c(1, 0), "stats": {"frequency": 100},
             "puces": ["puny_bulb"],
             "script": {1: [("summon", "puny_bulb", c(2, 1))]}},
        ],
    },
    {
        # Lance du soleil : degats puis recul de 4 cases loin du lanceur.
        "nom": "lance_recul",
        "tours": 2,
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"strength": 200, "frequency": 200},
             "armes": ["sun_spear"],
             "script": {1: [("weapon", "sun_spear", c(2, 0))]}},
            {"nom": "B", "camp": 2, "cellule": c(2, 0)},
            {"nom": "C", "camp": 2, "cellule": c(3, 0)},
        ],
    },
    {
        # Trebuchet : cercle 3 sans ligne de vue, recharge 5 (catalogue de l'API).
        "nom": "trebuchet",
        "tours": 2,
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"strength": 150, "frequency": 200},
             "puces": ["trebuchet"],
             "script": {1: [("chip", "trebuchet", c(6, 0))]}},
            {"nom": "D", "camp": 1, "cellule": c(1, 0)},
            {"nom": "B", "camp": 2, "cellule": c(6, 0)},
            {"nom": "C", "camp": 2, "cellule": c(7, 2)},
        ],
    },
]

SCENARIOS_300 = HEMORRAGIE + PLANTES + PROPRIETAIRE + ARMES

GENERATION = [
    {
        # Les actions 3.00 sont-elles GENEREES depuis un etat reel ? A porte les sept puces ; B
        # ennemi a portee, C bulbe allie pour Maturation. Liste des items des actions generees.
        "nom": "generation_300",
        "tours": 2,
        "entites": [
            {"nom": "A", "camp": 1, "cellule": c(0, 0), "stats": {"frequency": 200, "tp": 40},
             "puces": ["hemorrhage", "superinfection", "maturation", "corn", "chilli_pepper",
                       "prototaxite", "trebuchet", "puny_bulb", "venom"],
             "script": {1: [("summon", "puny_bulb", c(0, 2)), ("chip", "venom", c(2, 0)), ("gen",)]}},
            {"nom": "B", "camp": 2, "cellule": c(2, 0)},
        ],
    },
]

SCENARIOS_300 = SCENARIOS_300 + GENERATION

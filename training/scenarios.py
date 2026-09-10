"""Formats de combat, compositions et BLOCS de quatre combats.

Le bloc est l'unite statistique du harnais. Contre un meme adversaire O, avec la meme graine,
la meme carte et les memes builds attaches aux memes creneaux, on joue :

    1. C a gauche, O a droite        3. H a gauche, O a droite
    2. O a gauche, C a droite        4. O a gauche, H a droite

    x(C) = moyenne des resultats de C en 1 et 2
    x(H) = moyenne des resultats de H en 3 et 4
    d    = x(C) - x(H)                                  dans [-1, +1]

Deux consequences a ne jamais perdre de vue :

- **Les quatre combats ne sont PAS quatre observations.** Les deux miroirs d'une meme graine
  sont correles, et les statistiques portent sur les `d`, un par bloc.
- **Les combats H/O se reutilisent** pour tous les candidats evalues sur ce bloc. Apres
  remplissage du cache, un candidat de plus ne coute que deux combats par bloc. C'est le
  principal gain de debit du harnais.

Une graine commune ne garantit pas les memes tirages une fois que les actions divergent.
L'appariement reduit la variance, il ne la supprime pas.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parent
BUILDS = RACINE / "data" / "meta_builds.jsonl"

# Table « Fight types » du moteur (State.java) : type 0 solo, 1 eleveur, 2 team, 3 BR.
# A ne pas confondre avec la table « Fight full types » (TYPE_SOLO_GARDEN = 1, ...), qui est
# une notion interne combinant type et contexte. Se tromper de table donne un scenario qui
# tourne sans erreur dans le mauvais mode.
TYPE_SOLO, TYPE_FARMER, TYPE_TEAM, TYPE_BR = 0, 1, 2, 3
CONTEXTE_JARDIN, CONTEXTE_BR = 2, 5

# Limite normale du moteur (State.MAX_TURNS). Les combats de decision vont jusqu'a leur fin
# normale : un combat tronque est un controle technique, pas une mesure de qualite.
TOURS_MAX = 64


@dataclass(frozen=True)
class Format:
    nom: str
    type_moteur: int
    contexte: int
    poireaux_par_camp: int
    eleveurs_par_camp: int
    synthetique: bool = False
    note: str = ""


FORMATS = {
    "solo": Format("solo", TYPE_SOLO, CONTEXTE_JARDIN, 1, 1),
    "farmer": Format("farmer", TYPE_FARMER, CONTEXTE_JARDIN, 4, 1),
    # La composition team est SYNTHETIQUE : deux eleveurs de deux poireaux par camp. Le
    # snapshot du meta donne des eleveurs, pas les equipes reellement jouees par Aurel. Le
    # rapport doit le dire — une team inventee n'est pas automatiquement representative.
    "team": Format("team", TYPE_TEAM, CONTEXTE_JARDIN, 4, 2, synthetique=True,
                   note="Deux eleveurs de deux poireaux par camp, composes depuis le snapshot "
                        "du meta. Aucune donnee de la team reellement jouee n'est disponible."),
    "br": Format("br", TYPE_BR, CONTEXTE_BR, 1, 1),
}


@dataclass(frozen=True)
class Bloc:
    """Une unite statistique : un format, un adversaire, une graine, des builds figes."""
    format: str
    indice: int
    graine: int
    adversaire: str                  # identifiant de la politique O dans la ligue
    builds_gauche: tuple[int, ...]
    builds_droite: tuple[int, ...]
    eleveurs_gauche: tuple[int, ...] = field(default=())
    eleveurs_droite: tuple[int, ...] = field(default=())

    def cle(self) -> str:
        brut = json.dumps({
            "format": self.format, "indice": self.indice, "graine": self.graine,
            "adversaire": self.adversaire,
            "g": list(self.builds_gauche), "d": list(self.builds_droite),
            "eg": list(self.eleveurs_gauche), "ed": list(self.eleveurs_droite),
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:16]


def charger_builds(chemin: Path = BUILDS) -> dict[int, dict[str, Any]]:
    """[id -> build] du snapshot du meta. Ce sont des BUILDS, pas les IA de ces joueurs."""
    builds = {}
    with open(chemin, encoding="utf-8") as f:
        for ligne in f:
            if '"build"' not in ligne and '"stats"' not in ligne:
                continue
            b = json.loads(ligne)
            if "stats" not in b:
                continue
            builds[b["id"]] = b
    if not builds:
        raise ValueError("aucun build lisible dans %s" % chemin)
    return builds


def _par_eleveur(builds: dict[int, dict[str, Any]]) -> dict[int, list[int]]:
    groupes: dict[int, list[int]] = {}
    for bid, b in builds.items():
        groupes.setdefault(b["farmer"]["id"], []).append(bid)
    for ids in groupes.values():
        ids.sort()
    return groupes


def plan_de_blocs(format_nom: str, adversaires: list[str], nb_blocs: int,
                  graine_campagne: int, builds: dict[int, dict[str, Any]],
                  decalage: int = 0) -> list[Bloc]:
    """Blocs deterministes, repartis a poids egal entre les adversaires.

    `decalage` fait avancer le plan sans rejouer les memes graines : c'est ce qui rend les
    graines de CONFIRMATION reellement neuves, tirees apres le gel du candidat et jamais
    utilisees pour le choisir.
    """
    fmt = FORMATS[format_nom]
    groupes = _par_eleveur(builds)
    complets = sorted(e for e, ids in groupes.items() if len(ids) >= fmt.poireaux_par_camp)
    if len(complets) < 2:
        raise ValueError("pas assez d'eleveurs complets pour le format %s" % format_nom)

    blocs = []
    for i in range(nb_blocs):
        indice = decalage + i
        rnd = random.Random("%d|%s|%d" % (graine_campagne, format_nom, indice))
        adversaire = adversaires[indice % len(adversaires)]
        graine = rnd.randrange(1, 2_000_000_000)
        if fmt.eleveurs_par_camp == 1:
            a, b = rnd.sample(complets, 2)
            g = tuple(groupes[a][:fmt.poireaux_par_camp])
            d = tuple(groupes[b][:fmt.poireaux_par_camp])
            eg, ed = (a,), (b,)
        else:
            # Team : plusieurs eleveurs par camp, chacun apportant sa part de poireaux.
            par_eleveur = fmt.poireaux_par_camp // fmt.eleveurs_par_camp
            choisis = rnd.sample(complets, 2 * fmt.eleveurs_par_camp)
            gauche = choisis[:fmt.eleveurs_par_camp]
            droite = choisis[fmt.eleveurs_par_camp:]
            g = tuple(x for e in gauche for x in groupes[e][:par_eleveur])
            d = tuple(x for e in droite for x in groupes[e][:par_eleveur])
            eg, ed = tuple(gauche), tuple(droite)
        blocs.append(Bloc(format_nom, indice, graine, adversaire, g, d, eg, ed))
    return blocs


def _entite(build: dict[str, Any], entity_id: int, team: int, eleveur: int,
            chemin_ia: str) -> dict[str, Any]:
    s = build["stats"]
    return {
        "id": entity_id,
        "ai": chemin_ia,
        "ai_owner": eleveur,
        "name": build["name"],
        "type": 0,
        "farmer": eleveur,
        "team": team,
        "level": build["level"],
        "life": s["life"], "strength": s["strength"], "wisdom": s["wisdom"],
        "agility": s["agility"], "resistance": s["resistance"], "science": s["science"],
        "magic": s["magic"], "frequency": s["frequency"],
        # COEURS REELS du build. Aucun plancher, aucun plafond : le budget d'operations fait
        # partie de ce qu'on mesure. Un scoring plus cher perd de la recherche, et c'est le
        # combat qui doit le facturer, pas une garde separee.
        "cores": s["cores"],
        "ram": s["ram"], "tp": s["tp"], "mp": s["mp"],
        "weapons": [i["template"] for i in build["weapons"]],
        "chips": [i["template"] for i in build["chips"]],
    }


def scenario(bloc: Bloc, builds: dict[int, dict[str, Any]],
             ia_gauche: str, ia_droite: str) -> dict[str, Any]:
    """Scenario resolu. Les builds restent attaches a leurs creneaux ; seule l'affectation des
    politiques change d'une orientation a l'autre."""
    fmt = FORMATS[bloc.format]
    if bloc.format == "br":
        raise ValueError("la BR a son propre constructeur : scenario_br")

    if fmt.eleveurs_par_camp == 1:
        eleveurs = [{"id": 1, "name": "camp-gauche", "country": "fr"},
                    {"id": 2, "name": "camp-droite", "country": "fr"}]
        equipes = [{"id": 1, "name": "gauche"}, {"id": 2, "name": "droite"}]
        groupes = [
            [_entite(builds[b], 10000 + p, 1, 1, ia_gauche)
             for p, b in enumerate(bloc.builds_gauche, 1)],
            [_entite(builds[b], 20000 + p, 2, 2, ia_droite)
             for p, b in enumerate(bloc.builds_droite, 1)],
        ]
    else:
        # Team : les eleveurs sont distincts, les equipes les regroupent deux a deux.
        par = fmt.poireaux_par_camp // fmt.eleveurs_par_camp
        eleveurs, equipes, groupes = [], [{"id": 1, "name": "gauche"}, {"id": 2, "name": "droite"}], []
        eid = 1
        for camp, (ids, ia) in enumerate(((bloc.builds_gauche, ia_gauche),
                                          (bloc.builds_droite, ia_droite)), 1):
            for k in range(fmt.eleveurs_par_camp):
                eleveur = camp * 10 + k
                eleveurs.append({"id": eleveur, "name": "camp%d-eleveur%d" % (camp, k), "country": "fr"})
                groupes.append([
                    _entite(builds[b], camp * 10000 + eid + j, camp, eleveur, ia)
                    for j, b in enumerate(ids[k * par:(k + 1) * par])
                ])
                eid += par
    return {
        "farmers": eleveurs,
        "teams": equipes,
        "entities": groupes,
        "fight_type": fmt.type_moteur,
        "fight_context": fmt.contexte,
        "random_seed": bloc.graine,
        "max_turns": TOURS_MAX,
    }


def ecrire(chemin: Path, valeur: dict[str, Any]) -> None:
    """Ecriture ATOMIQUE : un worker ne doit jamais lire un scenario a moitie ecrit.
    Repris de l'export atomique de train_transition.py (branche hybride)."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    provisoire = chemin.with_suffix(chemin.suffix + ".partiel")
    provisoire.write_text(json.dumps(valeur, ensure_ascii=False, separators=(",", ":")),
                          encoding="utf-8")
    provisoire.replace(chemin)

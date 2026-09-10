"""Adaptateur d'optimiseur : interface, pas fournisseur.

Le harnais doit pouvoir etre livre et teste sans choisir de fournisseur ni depenser un centime
d'appel. Le mode par defaut est donc MANUEL : on enregistre un patch ecrit a la main, et tout
le reste de la chaine — evaluation, statistiques, promotion — fonctionne a l'identique.

Un optimiseur rend trois choses, jamais une seule :

1. un patch contre un PARENT identifie ;
2. une hypothese courte sur le comportement attendu et les formats concernes ;
3. les dependances de scoring, de coupe ou de cache qu'il a du modifier.

Le troisieme point n'est pas decoratif. Une nouvelle dependance du score peut exiger une cle
ou une invalidation supplementaire ; une whitelist trop etroite forcerait l'optimiseur a
ecrire un cache faux plutot qu'a declarer qu'il a besoin d'un helper. Toute sortie de perimetre
est donc classee EXTENSION A EXAMINER, et non mesuree comme un patch incomplet.

Ce que l'optimiseur ne peut PAS toucher : l'evaluateur, les seuils de promotion, les builds,
les cœurs, les resultats et les graines futures. Les manifestes de confirmation restent hors
de son contexte jusqu'a la decision.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Proposition:
    patch: str
    hypothese: str
    dependances: list[str] = field(default_factory=list)
    parent: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"patch": self.patch, "hypothese": self.hypothese,
                "dependances": self.dependances, "parent": self.parent, "meta": self.meta}


class Optimiseur(Protocol):
    def proposer(self, contexte: dict[str, Any]) -> dict[str, Any]: ...


class Manuel:
    """Mode par defaut : le patch vient d'un fichier ou de l'appelant.

    Sert aussi de double factice pour le test de reception du cycle complet : aucune API, aucun
    cout, et la meme forme de sortie qu'un vrai fournisseur.
    """

    def __init__(self, patch: str | None = None, hypothese: str | None = None,
                 dependances: list[str] | None = None):
        self._patch = patch if patch is not None else "# patch vide : aucun changement propose\n"
        self._hypothese = hypothese or "aucune hypothese fournie (mode manuel)"
        self._dependances = list(dependances or [])

    def proposer(self, contexte: dict[str, Any]) -> dict[str, Any]:
        return Proposition(
            patch=self._patch,
            hypothese=self._hypothese,
            dependances=self._dependances,
            parent=str(contexte.get("champion", "")),
            meta={"mode": "manuel", "cout": 0.0},
        ).as_dict()


def classer_portee(fichiers: list[str], portee: list[str],
                   hors_portee: list[str]) -> tuple[str, list[str]]:
    """(verdict, fichiers en cause). Verdict : DANS_PORTEE ou EXTENSION_A_EXAMINER.

    Un patch qui sort du perimetre n'est pas mesure a moitie : il est classe comme extension a
    examiner. Mesurer un patch tronque donnerait un resultat qui ne correspond a aucune idee.
    """
    fautifs = []
    for f in fichiers:
        f = f.replace("\\", "/")
        if any(fnmatch.fnmatch(f, m) for m in hors_portee):
            fautifs.append(f)
            continue
        if not any(fnmatch.fnmatch(f, m) for m in portee):
            fautifs.append(f)
    return ("DANS_PORTEE" if not fautifs else "EXTENSION_A_EXAMINER"), fautifs

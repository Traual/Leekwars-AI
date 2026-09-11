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
import hashlib
import re
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

    def source(self) -> str:
        """Le patch, CONNU SANS APPELER `proposer`. Sert a demontrer la provenance d'une
        branche ancienne sans solliciter un fournisseur."""
        return self._patch

    def empreinte_prevue(self) -> str:
        """L'empreinte de la source, CONNUE SANS APPELER `proposer`.

        Elle permet a la boucle de reconnaitre un candidat deja constitue sans demander une
        proposition de plus : un fournisseur paye ne doit pas etre sollicite pour retrouver un
        travail deja fait. Un fournisseur qui ne peut pas la predire n'expose pas cette
        methode, et seule l'identite du parent est alors verifiee.
        """
        return hashlib.sha256(self._patch.encode("utf-8")).hexdigest()

    def proposer(self, contexte: dict[str, Any]) -> dict[str, Any]:
        return Proposition(
            patch=self._patch,
            hypothese=self._hypothese,
            dependances=self._dependances,
            parent=str(contexte.get("champion", "")),
            meta={"mode": "manuel", "cout": 0.0,
                  "secondes_restantes": contexte.get("secondes_restantes")},
        ).as_dict()


DANS_PORTEE = "DANS_PORTEE"
EXTENSION_A_EXAMINER = "EXTENSION_A_EXAMINER"
EXTENSION_AUTORISEE = "EXTENSION_AUTORISEE"
INVARIANT_ROMPU = "INVARIANT_ROMPU"

# Verdicts qui INTERDISENT d'evaluer le candidat. Une extension non resolue reste dans son
# etat : elle ne participe ni au crible, ni a la confirmation, ni a la promotion. Mesurer un
# patch hors perimetre donnerait un resultat qui ne correspond a aucune idee convenue, et
# `run-loop` enchainait justement l'enregistrement et l'evaluation sans lire ce verdict.
VERDICTS_BLOQUANTS = (EXTENSION_A_EXAMINER, INVARIANT_ROMPU)


def classer_portee(fichiers: list[str], portee: list[str],
                   hors_portee: list[str]) -> tuple[str, list[str]]:
    """(verdict, fichiers en cause). Verdict : DANS_PORTEE ou EXTENSION_A_EXAMINER.

    Un patch qui sort du perimetre n'est pas mesure a moitie : il est classe comme extension a
    examiner. L'autorisation d'un helper necessaire existe, mais elle est EXPLICITE et
    journalisee — voir `candidates.enregistrer(autorisation=...)`.
    """
    fautifs = []
    for f in fichiers:
        f = f.replace("\\", "/")
        if any(fnmatch.fnmatch(f, m) for m in hors_portee):
            fautifs.append(f)
            continue
        if not any(fnmatch.fnmatch(f, m) for m in portee):
            fautifs.append(f)
    return (DANS_PORTEE if not fautifs else EXTENSION_A_EXAMINER), fautifs


def verifier_invariants(lire_fichier, invariants: dict[str, list[dict[str, Any]]]
                        ) -> list[str]:
    """Controle le perimetre EFFECTIF a l'interieur des fichiers autorises.

    Autoriser `BFS.leek` en entier autorisait aussi a retirer les gardes du budget interne
    d'operations — ce qui n'est pas la coupe, et ce qu'aucune mesure de combat ne rattrape,
    puisqu'une IA sans garde ne perd pas de la recherche : elle se fait couper par le moteur.

    Chaque invariant est un motif et un nombre minimal d'occurrences dans le fichier tel qu'il
    est chez le candidat. `lire_fichier(chemin)` rend le contenu ou None.
    """
    ruptures: list[str] = []
    for chemin, regles in (invariants or {}).items():
        contenu = lire_fichier(chemin)
        if contenu is None:
            ruptures.append("%s : fichier absent du candidat alors qu'il porte un invariant"
                            % chemin)
            continue
        for regle in regles:
            motif = regle["motif"]
            minimum = int(regle.get("minimum", 1))
            trouves = len(re.findall(motif, contenu))
            if trouves < minimum:
                ruptures.append(
                    "%s : %d occurrence(s) de /%s/, il en faut au moins %d — %s"
                    % (chemin, trouves, motif, minimum,
                       regle.get("raison", "invariant de perimetre")))
    return ruptures

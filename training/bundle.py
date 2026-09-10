"""Empreinte d'un bundle d'IA, et materialisation d'un bundle en lecture seule.

Un « bundle » est le contenu de `New_AI/` a un commit donne. Deux exigences le gouvernent :

1. **L'empreinte doit etre reproductible sur n'importe quel clone.** Elle est donc calculee
   sur les OBJETS GIT et jamais sur l'arbre de travail : `core.autocrlf` reecrit les fins de
   ligne au checkout, donc hacher les fichiers du disque donnerait une empreinte differente
   d'une machine a l'autre pour un code identique.

2. **Elle doit couvrir tout ce qui influence le jeu.** Pas seulement `Scoring.leek`, pas
   seulement un vecteur de poids : tout fichier du bundle. Un changement d'include ou d'un
   helper de coupe doit invalider les resultats au meme titre qu'un changement de poids.

L'ancien harnais hachait `NNModel.leek`, les parametres et le JAR. Cette empreinte-la ne
voyait pas une modification de `Scoring.leek`, donc elle pouvait resservir un resultat pour un
code different. C'est le controle que le cahier des charges demande de remplacer.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

DEPOT = Path(__file__).resolve().parents[1]
SOUS_ARBRE = "New_AI"


def _git(*args: str, binaire: bool = False):
    r = subprocess.run(["git", "-C", str(DEPOT), *args],
                       capture_output=True, check=True)
    return r.stdout if binaire else r.stdout.decode("utf-8")


def lister(commit: str) -> list[tuple[str, str]]:
    """[(chemin, sha du blob)] du bundle a ce commit, trie par chemin."""
    sortie = _git("ls-tree", "-r", "--full-tree", commit, SOUS_ARBRE).strip()
    if not sortie:
        raise ValueError("aucun fichier %s au commit %s" % (SOUS_ARBRE, commit))
    entrees = []
    for ligne in sortie.split("\n"):
        meta, chemin = ligne.split("\t", 1)
        _mode, _type, blob = meta.split()
        entrees.append((chemin, blob))
    entrees.sort()
    return entrees


def empreinte(commit: str) -> dict:
    """Empreinte du bundle : sha256 sur '<chemin>\\0<sha256 du contenu>\\n' trie par chemin.

    L'arbre git est rendu en plus. Les deux se verifient l'un l'autre : l'arbre vient de Git
    et de son SHA-1, l'empreinte d'un calcul independant en SHA-256. Un desaccord signale une
    corruption ou un bug de ce module, jamais un changement legitime.
    """
    entrees = lister(commit)
    h = hashlib.sha256()
    for chemin, blob in entrees:
        contenu = _git("cat-file", "blob", blob, binaire=True)
        h.update(chemin.encode("utf-8") + b"\0" + hashlib.sha256(contenu).hexdigest().encode() + b"\n")
    return {
        "commit": _git("rev-parse", commit).strip(),
        "arbre_git": _git("rev-parse", "%s:%s" % (commit, SOUS_ARBRE)).strip(),
        "sha256": h.hexdigest(),
        "nb_fichiers": len(entrees),
    }


def materialiser(commit: str, destination: Path) -> Path:
    """Ecrit le bundle sur disque, a plat, tel qu'il est dans Git.

    La destination est nommee par l'empreinte par l'appelant, donc un bundle deja materialise
    n'est jamais reecrit : le generateur ressert un binaire compile quand un repertoire d'IA a
    deja servi, et reecrire sous un chemin deja compile donnerait un melange silencieux
    d'ancien et de neuf.
    """
    destination = Path(destination)
    if destination.exists():
        return destination
    provisoire = destination.with_name(destination.name + ".partiel")
    if provisoire.exists():
        import shutil
        shutil.rmtree(provisoire)
    for chemin, blob in lister(commit):
        relatif = chemin[len(SOUS_ARBRE) + 1:]
        cible = provisoire / relatif
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_bytes(_git("cat-file", "blob", blob, binaire=True))
    # Rendu visible seulement une fois complet : un worker ne doit jamais compiler un bundle
    # a moitie ecrit.
    provisoire.rename(destination)
    return destination


# --------------------------------------------------------------------------------------
# Bundles qui ne viennent pas de Git
# --------------------------------------------------------------------------------------

def empreinte_repertoire(racine: Path) -> dict:
    """Empreinte d'un arbre d'IA present sur disque, meme forme que celle d'un commit.

    Les dix politiques de la ligue sont des repertoires materialises, pas des commits. On hache
    donc les OCTETS des fichiers tels qu'ils sont : ces arbres ne passent pas par un checkout
    Git, donc aucune reecriture de fin de ligne ne peut les faire varier d'une machine a
    l'autre. La forme du resultat est identique a celle d'`empreinte`, pour que les deux
    origines se melangent sans cas particulier en aval.
    """
    racine = Path(racine)
    fichiers = sorted(p for p in racine.rglob("*") if p.is_file())
    if not fichiers:
        raise ValueError("repertoire de bundle vide : %s" % racine)
    h = hashlib.sha256()
    for p in fichiers:
        rel = str(p.relative_to(racine)).replace("\\", "/")
        h.update(rel.encode("utf-8") + b"\0"
                 + hashlib.sha256(p.read_bytes()).hexdigest().encode() + b"\n")
    return {"commit": None, "arbre_git": None, "source": str(racine),
            "sha256": h.hexdigest(), "nb_fichiers": len(fichiers)}


def copier(racine_source: Path, destination: Path) -> Path:
    """Copie un arbre sur disque, avec la meme discipline que `materialiser` : rien n'est
    ecrit sous un chemin deja complet, et la destination n'apparait qu'une fois entiere."""
    import shutil
    destination = Path(destination)
    if destination.exists():
        return destination
    provisoire = destination.with_name(destination.name + ".partiel")
    if provisoire.exists():
        shutil.rmtree(provisoire)
    shutil.copytree(Path(racine_source), provisoire)
    provisoire.rename(destination)
    return destination


if __name__ == "__main__":
    import json
    import sys
    ref = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    print(json.dumps(empreinte(ref), ensure_ascii=False, indent=2))

"""Enregistrement d'un candidat : un COMMIT immuable, son parent, son empreinte.

Un candidat n'est jamais un repertoire de travail qu'on modifie pendant l'evaluation. Il est
fige dans un commit sur sa propre branche, et c'est ce commit qui est mesure. Sans cela, une
retouche pendant une campagne rendrait les resultats inattribuables.

Les branches de candidats s'appellent `scoring-candidates/<id>` et surtout PAS `scoring/<id>` :
Git refuse de creer une reference sous un prefixe quand une branche du meme nom existe deja, et
`scoring` existe.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import bundle as mod_bundle
import optimizer as mod_opt

DEPOT = Path(__file__).resolve().parents[1]
PREFIXE_BRANCHE = "scoring-candidates"


def _git(*args: str, verifier: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(DEPOT), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if verifier and r.returncode != 0:
        raise RuntimeError("git %s : %s" % (" ".join(args), (r.stderr or r.stdout).strip()))
    return r.stdout


def arbre_propre() -> bool:
    return not _git("status", "--porcelain").strip()


def fichiers_du_diff(base: str, tete: str) -> list[str]:
    sortie = _git("diff", "--name-only", "%s..%s" % (base, tete)).strip()
    return [l for l in sortie.split("\n") if l]


def contenu_au_commit(commit: str, chemin: str) -> str | None:
    r = subprocess.run(["git", "-C", str(DEPOT), "show", "%s:%s" % (commit, chemin)],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else None


MARQUE_SOURCE = "Empreinte-source: "


def branche_de(ident: str) -> str:
    return "%s/%s" % (PREFIXE_BRANCHE, ident)


def branche_existe(ident: str) -> bool:
    return bool(_git("branch", "--list", branche_de(ident)).strip())


def _empreinte_du_commit(commit: str) -> str:
    """L'empreinte de la source, telle que le commit du candidat la porte.

    Elle est inscrite dans le message parce que Git est le seul des deux systemes qui survive
    a une coupure entre la creation du commit et l'insertion SQLite : sans elle, une branche
    orpheline ne pouvait plus etre reliee a la proposition qui l'avait produite.
    """
    message = _git("log", "-1", "--format=%B", commit)
    for ligne in message.split("\n"):
        if ligne.startswith(MARQUE_SOURCE):
            return ligne[len(MARQUE_SOURCE):].strip()
    return ""


def retrouver(ident: str, parent: str, empreinte_source: str = "") -> dict[str, Any] | None:
    """Le candidat deja COMMITE dont l'inscription au registre a ete interrompue.

    Le commit et la branche sont crees avant l'insertion SQLite. Si celle-ci echoue, le
    nettoyage d'`enregistrer` ne s'applique pas — la fonction a reussi — et la relance se
    heurtait a « la branche existe deja » sans jamais retrouver le candidat. On le reconstruit
    ici depuis Git, apres avoir verifie son parent et l'identite de sa proposition.
    """
    branche = branche_de(ident)
    if not branche_existe(ident):
        return None
    tete = _git("rev-parse", branche).strip()
    parent_resolu = _git("rev-parse", parent).strip()
    parents = _git("log", "-1", "--format=%P", tete).split()
    if not parents or parents[0] != parent_resolu:
        raise RuntimeError(
            "la branche %s existe mais part de %s, pas de %s : ce n'est pas une reprise du "
            "meme essai." % (branche, (parents[0] if parents else "?")[:12],
                             parent_resolu[:12]))
    portee_commit = _empreinte_du_commit(tete)
    if empreinte_source and portee_commit and portee_commit != empreinte_source:
        raise RuntimeError(
            "la branche %s porte une autre proposition (%s au lieu de %s) : un candidat est "
            "immuable, choisir un identifiant neuf." % (branche, portee_commit[:12],
                                                        empreinte_source[:12]))
    modifies = fichiers_du_diff(parent_resolu, tete)
    emp = mod_bundle.empreinte(tete)
    return {"id": ident, "branche": branche, "commit": tete, "parent": parent_resolu,
            "bundle_sha256": emp["sha256"], "arbre_git": emp["arbre_git"],
            "nb_fichiers": emp["nb_fichiers"], "fichiers_modifies": modifies,
            "empreinte_source": portee_commit or empreinte_source,
            "reconstruit_depuis_git": True}


def enregistrer(ident: str, parent: str, patch: Path | None = None,
                bundle_dir: Path | None = None, hypothese: str = "",
                portee: list[str] | None = None, hors_portee: list[str] | None = None,
                dependances: list[str] | None = None,
                invariants: dict[str, list[dict[str, Any]]] | None = None,
                autorisation: str = "", empreinte_source: str = "") -> dict[str, Any]:
    """Cree `scoring-candidates/<id>` depuis `parent`, y pose le changement, et commite.

    Deux entrees possibles, et une seule sortie : un commit. `patch` applique un diff unifie ;
    `bundle_dir` remplace `New_AI/` par un arbre deja constitue — le mode manuel accepte les
    deux, aucun fournisseur d'API n'est requis.
    """
    if not arbre_propre():
        raise RuntimeError("l'arbre de travail n'est pas propre : un candidat doit partir d'un "
                           "etat connu, sinon son commit contient autre chose que son idee.")
    branche = "%s/%s" % (PREFIXE_BRANCHE, ident)
    if _git("branch", "--list", branche).strip():
        raise RuntimeError("la branche %s existe deja : choisir un autre identifiant plutot que "
                           "d'ecraser un candidat deja evalue." % branche)

    depart = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
    _git("checkout", "-b", branche, parent)
    abouti = False
    try:
        if patch is not None:
            # Chemin ABSOLU : `git -C <depot>` resout un chemin relatif depuis la racine du
            # depot, pas depuis le repertoire courant de l'appelant.
            r = subprocess.run(["git", "-C", str(DEPOT), "apply", "--index",
                                str(Path(patch).resolve())],
                               capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
            if r.returncode != 0:
                raise RuntimeError("patch inapplicable sur %s : %s" % (parent, r.stderr.strip()))
        elif bundle_dir is not None:
            cible = DEPOT / "New_AI"
            shutil.rmtree(cible)
            shutil.copytree(Path(bundle_dir), cible)
            _git("add", "-A", "New_AI")
        else:
            raise ValueError("il faut un patch ou un repertoire de bundle")

        modifies = [l for l in _git("diff", "--cached", "--name-only").strip().split("\n") if l]
        verdict, fautifs = mod_opt.classer_portee(modifies, portee or ["New_AI/**"],
                                                  hors_portee or [])
        # On commite quand meme : le candidat reste trace. C'est l'APPELANT qui refuse de
        # l'evaluer tant que le verdict est bloquant. Une extension peut etre autorisee, mais
        # seulement de facon explicite et journalisee.
        if verdict != mod_opt.DANS_PORTEE and autorisation:
            verdict = mod_opt.EXTENSION_AUTORISEE
        message = ("candidat %s : %s\n\nParent : %s\nPortee : %s\nDependances declarees : %s\n"
                   % (ident, hypothese or "(aucune hypothese fournie)", parent, verdict,
                      ", ".join(dependances or []) or "aucune"))
        if autorisation:
            message += "Extension autorisee : %s\n" % autorisation
        # L'empreinte de la SOURCE voyage dans le commit : c'est le seul endroit qui survive a
        # une coupure entre Git et SQLite, et c'est elle qui permet de reconnaitre la meme
        # proposition a la reprise.
        if empreinte_source:
            message += "%s%s\n" % (MARQUE_SOURCE, empreinte_source)
        _git("commit", "-q", "-m", message)
        tete = _git("rev-parse", "HEAD").strip()

        # Le perimetre EFFECTIF a l'interieur des fichiers autorises : autoriser un fichier
        # n'autorise pas a y retirer les gardes du budget interne d'operations. Une IA sans
        # garde ne « perd pas de la recherche » : elle se fait couper par le moteur.
        ruptures = mod_opt.verifier_invariants(
            lambda chemin: contenu_au_commit(tete, chemin), invariants or {})
        if ruptures:
            verdict = mod_opt.INVARIANT_ROMPU

        emp = mod_bundle.empreinte(tete)
        abouti = True
        return {
            "id": ident, "branche": branche, "commit": tete,
            "parent": _git("rev-parse", parent).strip(),
            "bundle_sha256": emp["sha256"], "arbre_git": emp["arbre_git"],
            "nb_fichiers": emp["nb_fichiers"], "fichiers_modifies": modifies,
            "verdict_portee": verdict, "hors_portee": fautifs,
            "invariants_rompus": ruptures, "autorisation": autorisation,
            "empreinte_source": empreinte_source,
            "hypothese": hypothese, "dependances": list(dependances or []),
        }
    finally:
        if not abouti:
            # Une tentative ratee ne doit bruler ni l'arbre de travail ni l'identifiant : sans
            # ce menage, relancer la meme commande apres avoir corrige le patch se heurtait a
            # « la branche existe deja », sur un arbre reste sale.
            _git("reset", "-q", "--hard", parent, verifier=False)
        _git("checkout", "-q", depart, verifier=False)
        if not abouti:
            _git("branch", "-q", "-D", branche, verifier=False)

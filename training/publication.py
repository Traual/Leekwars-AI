"""Publication d'un champion : commit, tag, manifeste, pointeur — et reprise apres coupure.

Git et SQLite ne partagent pas de transaction. Une promotion fait plusieurs ecritures dans
deux systemes, et une interruption au milieu laisserait soit un champion annonce dont le code
est introuvable, soit un commit orphelin qu'une reprise promouvrait deux fois.

La parade tient en deux regles.

**Le pointeur du champion actif est ecrit EN DERNIER.** Tant que le commit n'existe pas, que
son bundle n'a pas ete verifie et que le tag n'est pas pose, `current.json` continue de
designer le champion precedent. La version precedente ecrivait ce pointeur avant le commit :
une coupure juste avant le commit faisait deja lire champion-001 alors qu'aucun commit ni tag
n'existait.

**Un journal a etats, tenu dans SQLite AVANT chaque ecriture Git**, et une reprise qui lit
l'etat REEL de Git plutot que de faire confiance au journal :

    ouverte -> code_prepare -> bundle_verifie -> commit_ecrit -> tag_ecrit
            -> pointeur_ecrit -> terminee

La reprise aboutit toujours a l'un des deux etats surs : la publication est terminee, ou le
champion precedent est reellement conserve — pointeur restaure, arbre de travail nettoye,
manifeste orphelin retire. Le controle qui decide est le meme dans les deux cas : l'arbre
`New_AI` du commit de champion doit etre EXACTEMENT celui qui a ete evalue.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import bundle as mod_bundle

DEPOT = Path(__file__).resolve().parents[1]
CHAMPIONS = Path(__file__).resolve().parent / "champions"
BRANCHE = "scoring"

ETAPES = ("ouverte", "code_prepare", "bundle_verifie", "commit_ecrit", "tag_ecrit",
          "pointeur_ecrit", "terminee")


class CoupureSimulee(RuntimeError):
    """Interruption provoquee par un test de reception, a une frontiere precise."""


def _git(*args: str, depot: Path | None = None, verifier: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(depot or DEPOT), *args], capture_output=True, text=True)
    if verifier and r.returncode != 0:
        raise RuntimeError("git %s : %s" % (" ".join(args), (r.stderr or r.stdout).strip()))
    return r.stdout


def prochain_identifiant(champions: Path | None = None) -> str:
    champions = Path(champions or CHAMPIONS)
    n = -1
    for p in Path(champions).glob("champion-*.json"):
        try:
            n = max(n, int(p.stem.split("-")[1]))
        except (IndexError, ValueError):
            continue
    return "champion-%03d" % (n + 1)


def tag_existe(nom: str, depot: Path | None = None) -> bool:
    return bool(_git("tag", "-l", nom, depot=depot).strip())


def _commit_existe(ref: str, depot: Path | None = None) -> bool:
    if not ref:
        return False
    r = subprocess.run(["git", "-C", str(depot or DEPOT), "cat-file", "-e", "%s^{commit}" % ref],
                       capture_output=True, text=True)
    return r.returncode == 0


def _ecrire_pointeur(champions: Path, nouveau: str, candidat: dict[str, Any], tag: str) -> dict:
    pointeur = {"champion_courant": nouveau,
                "manifeste": "training/champions/%s.json" % nouveau,
                "commit_code": candidat["commit"],
                "bundle_sha256": candidat["bundle_sha256"], "tag": tag,
                "_role": "Seul le controleur ecrit ce pointeur ; l'optimiseur n'y touche pas."}
    (Path(champions) / "current.json").write_text(
        json.dumps(pointeur, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return pointeur


def publier(reg, candidat: dict[str, Any], champion_attendu: str, decision: dict[str, Any],
            protocole: dict[str, Any], champions: Path | None = None,
            depot: Path | None = None, confirmation: int | None = None,
            _coupure: str | None = None) -> dict[str, Any]:
    """Promeut le candidat. Toute etape deja faite est constatee, pas refaite.

    `_coupure` est un point d'arret de RECEPTION : le nom d'une etape a laquelle lever, pour
    verifier qu'une interruption a cette frontiere precise laisse un etat rattrapable. Il n'a
    aucun usage en production et vaut None partout ailleurs.
    """
    depot = Path(depot or DEPOT)
    champions = Path(champions or CHAMPIONS)
    nouveau = prochain_identifiant(champions)
    tag = "scoring/%s" % nouveau

    def _peut_etre_coupe(etape: str) -> None:
        if _coupure == etape:
            raise CoupureSimulee("interruption de reception a l'etape %s" % etape)

    pub = reg.ouvrir_publication(candidat["id"], champion_attendu, nouveau, confirmation)
    depart = _git("rev-parse", "--abbrev-ref", "HEAD", depot=depot).strip()
    try:
        _git("checkout", "-q", BRANCHE, depot=depot)

        # 1. Le CODE du candidat et son manifeste, dans l'arbre de travail. Le pointeur du
        #    champion actif n'est PAS touche ici.
        reg.etape_publication(pub, "code_prepare", nouveau)
        _peut_etre_coupe("code_prepare")
        _git("checkout", candidat["commit"], "--", "New_AI", depot=depot)

        manifeste = {
            "id": nouveau, "statut": "promu",
            "promu_le": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "code": {"commit_candidat": candidat["commit"],
                     "branche_candidat": candidat.get("branche"),
                     "bundle_sha256": candidat["bundle_sha256"],
                     "arbre_git_New_AI": candidat.get("arbre_git"),
                     "parent": candidat.get("parent")},
            "hypothese": candidat.get("hypothese", ""),
            "champion_precedent": champion_attendu,
            "protocole": protocole,
            "resultats": decision,
            "_note": ("Le code enregistre ici est EXACTEMENT celui qui a ete evalue : la "
                      "publication verifie l'empreinte de l'arbre AVANT de commiter, "
                      "et n'ecrit rien sinon."),
        }
        chemin_manifeste = champions / ("%s.json" % nouveau)
        chemin_manifeste.write_text(json.dumps(manifeste, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")

        # 2. Controle AVANT le commit : l'arbre prepare est-il celui qui a ete mesure ?
        #    Verifier apres coup laissait un commit de champion errone dans l'histoire de
        #    `scoring`, qu'aucune reprise ne pouvait retirer proprement. L'empreinte se calcule
        #    aussi bien sur l'arbre de l'index que sur un commit.
        reg.etape_publication(pub, "bundle_verifie", nouveau)
        _peut_etre_coupe("bundle_verifie")
        _git("add", "-A", "New_AI", "training/champions", depot=depot)
        arbre = _git("write-tree", depot=depot).strip()
        emp = mod_bundle.empreinte(arbre)
        if emp["sha256"] != candidat["bundle_sha256"]:
            raise RuntimeError(
                "le bundle prepare (%s) differe de celui evalue (%s). Rien n'est commite : la "
                "promotion est annulee et il faut une nouvelle evaluation."
                % (emp["sha256"][:16], candidat["bundle_sha256"][:16]))

        # 3. Commit du code et du manifeste.
        reg.etape_publication(pub, "commit_ecrit", nouveau)
        _peut_etre_coupe("commit_ecrit")
        _git("commit", "-q", "-m",
             "scoring: promouvoir %s\n\nCandidat %s, parent %s.\nBundle %s.\n"
             % (nouveau, candidat["id"], champion_attendu, candidat["bundle_sha256"][:16]),
             depot=depot)
        commit_champion = _git("rev-parse", "HEAD", depot=depot).strip()

        # 4. Tag annote.
        reg.etape_publication(pub, "tag_ecrit", tag, commit_champion=commit_champion)
        _peut_etre_coupe("tag_ecrit")
        if not tag_existe(tag, depot=depot):
            _git("tag", "-a", tag, "-m",
                 "%s — promu depuis %s\nBundle %s\n" % (nouveau, candidat["id"], emp["sha256"]),
                 depot=depot)

        # 5. SEULEMENT MAINTENANT : le champion actif change.
        reg.etape_publication(pub, "pointeur_ecrit", tag, commit_champion=commit_champion)
        _peut_etre_coupe("pointeur_ecrit")
        _ecrire_pointeur(champions, nouveau, candidat, tag)
        _git("add", "-A", "training/champions", depot=depot)
        _git("commit", "-q", "-m", "scoring: pointer le champion %s\n" % nouveau, depot=depot)

        reg.cloturer_publication(pub, {"id": nouveau, "commit_code": candidat["commit"],
                                       "bundle_sha256": candidat["bundle_sha256"], "tag": tag,
                                       "precedent": champion_attendu,
                                       "manifeste": str(chemin_manifeste)})
        _git("checkout", "-q", depart, depot=depot, verifier=False)
        return {"champion": nouveau, "tag": tag, "commit_champion": commit_champion,
                "manifeste": str(chemin_manifeste), "precedent": champion_attendu}
    except BaseException as e:
        # L'arbre est laisse TEL QUEL : c'est ce qu'une vraie coupure produit, et c'est a la
        # reprise de le rattraper. Le journal dit ou on en etait.
        reg.etape_publication(pub, reg.publication_en_cours()["etape"] if
                              reg.publication_en_cours() else "echec",
                              "interrompue : %s" % str(e)[:200])
        raise


def _nettoyer_arbre(champions: Path, depot: Path, nouveau: str,
                    pointeur_precedent: dict[str, Any] | None) -> list[str]:
    """Remet l'arbre de travail et le pointeur dans l'etat d'avant la publication."""
    faits = []
    sale = _git("status", "--porcelain", "New_AI", "training/champions",
                depot=depot, verifier=False).strip()
    if sale:
        _git("reset", "-q", "HEAD", "--", "New_AI", "training/champions",
             depot=depot, verifier=False)
        _git("checkout", "-q", "--", "New_AI", "training/champions",
             depot=depot, verifier=False)
        faits.append("arbre de travail restaure")
    orphelin = Path(champions) / ("%s.json" % nouveau)
    if orphelin.exists():
        suivi = _git("ls-files", "--error-unmatch", "training/champions/%s.json" % nouveau,
                     depot=depot, verifier=False).strip()
        if not suivi:
            orphelin.unlink()
            faits.append("manifeste orphelin %s retire" % nouveau)
    if pointeur_precedent:
        actuel = Path(champions) / "current.json"
        if not actuel.exists() or json.loads(actuel.read_text(encoding="utf-8")) \
                .get("champion_courant") != pointeur_precedent.get("champion_courant"):
            actuel.write_text(json.dumps(pointeur_precedent, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
            faits.append("pointeur restaure sur %s" % pointeur_precedent.get("champion_courant"))
    return faits


def reconcilier(reg, champions: Path | None = None, depot: Path | None = None) -> dict[str, Any]:
    """Termine ou ABANDONNE une publication interrompue. N'en cree jamais une seconde.

    La verite est dans GIT, pas dans le journal : le journal dit ou on en etait, Git dit ce qui
    existe. Deux issues seulement, et jamais d'etat intermediaire laisse en place :

    - le commit de champion existe et son bundle est celui qui a ete evalue : le tag et le
      pointeur sont completes, la publication est close ;
    - sinon la publication est abandonnee, l'arbre est restaure et le champion actif reste
      REELLEMENT le precedent.
    """
    depot = Path(depot or DEPOT)
    champions = Path(champions or CHAMPIONS)
    en_cours = reg.publication_en_cours()
    if en_cours is None:
        return {"etat": "rien_a_reconcilier"}

    nouveau = en_cours["nouveau_champion"]
    tag = "scoring/%s" % nouveau
    precedent = json.loads(en_cours["pointeur_precedent"]) if en_cours["pointeur_precedent"] \
        else None
    depart = _git("rev-parse", "--abbrev-ref", "HEAD", depot=depot).strip()
    _git("checkout", "-q", BRANCHE, depot=depot, verifier=False)
    try:
        commit = ""
        if tag_existe(tag, depot=depot):
            commit = _git("rev-list", "-n", "1", tag, depot=depot).strip()
        elif _commit_existe(en_cours["commit_champion"] or "", depot=depot):
            commit = (en_cours["commit_champion"] or "").strip()

        if not commit:
            faits = _nettoyer_arbre(champions, depot, nouveau, precedent)
            reg.abandonner_publication(
                en_cours["id"],
                "interrompue avant le commit de champion (etape %s)" % en_cours["etape"])
            return {"etat": "abandonnee", "champion_actif": reg.champion_courant()
                    ["champion_courant"], "champion_abandonne": nouveau,
                    "etape_atteinte": en_cours["etape"], "remises_en_etat": faits,
                    "detail": ("Rien n'a ete promu : aucun commit de champion n'existait. Le "
                               "champion actif est reellement le precedent.")}

        emp = mod_bundle.empreinte(commit)
        brut = _git("show", "%s:training/champions/%s.json" % (commit, nouveau),
                    depot=depot, verifier=False)
        manifeste = json.loads(brut) if brut.strip() else None
        attendu = (manifeste or {}).get("code", {}).get("bundle_sha256")
        if manifeste is None or emp["sha256"] != attendu:
            faits = _nettoyer_arbre(champions, depot, nouveau, precedent)
            reg.abandonner_publication(
                en_cours["id"], "bundle du commit %s incoherent avec le manifeste" % commit[:12])
            return {"etat": "incoherente", "champion_abandonne": nouveau,
                    "champion_actif": reg.champion_courant()["champion_courant"],
                    "remises_en_etat": faits,
                    "detail": ("Le bundle du commit de champion ne correspond pas au manifeste : "
                               "rien n'est promu, une nouvelle evaluation est necessaire.")}

        # Le commit est bon : on TERMINE ce qui manque.
        faits = []
        if not tag_existe(tag, depot=depot):
            _git("tag", "-a", tag, "-m", "%s — promu (reprise)\nBundle %s\n"
                 % (nouveau, emp["sha256"]), commit, depot=depot)
            faits.append("tag %s pose" % tag)
        chemin_manifeste = champions / ("%s.json" % nouveau)
        if not chemin_manifeste.exists():
            chemin_manifeste.write_text(brut, encoding="utf-8")
            faits.append("manifeste restaure depuis le commit")
        pointeur = json.loads((champions / "current.json").read_text(encoding="utf-8"))
        if pointeur.get("champion_courant") != nouveau:
            _ecrire_pointeur(champions, nouveau,
                             {"commit": manifeste["code"]["commit_candidat"],
                              "bundle_sha256": manifeste["code"]["bundle_sha256"]}, tag)
            _git("add", "-A", "training/champions", depot=depot, verifier=False)
            _git("commit", "-q", "-m", "scoring: pointer le champion %s (reprise)\n" % nouveau,
                 depot=depot, verifier=False)
            faits.append("pointeur ecrit sur %s" % nouveau)
        reg.cloturer_publication(en_cours["id"],
                                 {"id": nouveau, "commit_code": manifeste["code"]["commit_candidat"],
                                  "bundle_sha256": manifeste["code"]["bundle_sha256"], "tag": tag,
                                  "precedent": en_cours["champion_attendu"],
                                  "manifeste": str(chemin_manifeste)})
        return {"etat": "terminee_par_reprise", "champion": nouveau, "tag": tag,
                "commit_champion": commit, "remises_en_etat": faits}
    finally:
        _git("checkout", "-q", depart, depot=depot, verifier=False)

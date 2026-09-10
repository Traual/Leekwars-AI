"""Publication d'un champion : commit, tag, manifeste, pointeur — et reprise apres coupure.

Git et SQLite ne partagent pas de transaction. Une promotion fait quatre ecritures dans deux
systemes, et une interruption au milieu laisserait soit un champion annonce dont le code est
introuvable, soit un commit orphelin qu'une reprise promouvrait deux fois.

La parade est un journal a etats, tenu dans SQLite AVANT chaque ecriture Git, et une reprise
qui lit l'etat REEL de Git plutot que de faire confiance au journal :

    ouverte -> manifeste_ecrit -> commit_ecrit -> tag_ecrit -> terminee

Chaque etape est IDEMPOTENTE. La reprise reconstate ce qui existe deja et ne refait que ce qui
manque. Le controle final est le seul qui compte : l'arbre `New_AI` du commit de champion doit
etre EXACTEMENT celui qui a ete evalue. Une resolution de conflit ou une retouche qui change le
bundle invalide la promotion et demande une nouvelle evaluation.
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


def _git(*args: str, depot: Path | None = None, verifier: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(depot or DEPOT), *args], capture_output=True, text=True)
    if verifier and r.returncode != 0:
        raise RuntimeError("git %s : %s" % (" ".join(args), (r.stderr or r.stdout).strip()))
    return r.stdout


def prochain_identifiant(champions: Path = CHAMPIONS) -> str:
    n = -1
    for p in Path(champions).glob("champion-*.json"):
        try:
            n = max(n, int(p.stem.split("-")[1]))
        except (IndexError, ValueError):
            continue
    return "champion-%03d" % (n + 1)


def tag_existe(nom: str, depot: Path | None = None) -> bool:
    return bool(_git("tag", "-l", nom, depot=depot).strip())


def publier(reg, candidat: dict[str, Any], champion_attendu: str, decision: dict[str, Any],
            protocole: dict[str, Any], champions: Path = CHAMPIONS,
            depot: Path | None = None) -> dict[str, Any]:
    """Promeut le candidat. Toute etape deja faite est constatee, pas refaite."""
    depot = Path(depot or DEPOT)
    champions = Path(champions)
    nouveau = prochain_identifiant(champions)
    tag = "scoring/%s" % nouveau

    pub = reg.ouvrir_publication(candidat["id"], champion_attendu, nouveau)
    try:
        depart = _git("rev-parse", "--abbrev-ref", "HEAD", depot=depot).strip()
        _git("checkout", BRANCHE, depot=depot)

        # 1. Le CODE du candidat devient celui de scoring.
        reg.etape_publication(pub, "manifeste_ecrit", nouveau)
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
                      "publication verifie l'empreinte du bundle apres le commit, et echoue "
                      "sinon."),
        }
        chemin_manifeste = champions / ("%s.json" % nouveau)
        chemin_manifeste.write_text(json.dumps(manifeste, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
        pointeur = {"champion_courant": nouveau,
                    "manifeste": "training/champions/%s.json" % nouveau,
                    "commit_code": candidat["commit"],
                    "bundle_sha256": candidat["bundle_sha256"], "tag": tag,
                    "_role": "Seul le controleur ecrit ce pointeur ; l'optimiseur n'y touche pas."}
        (champions / "current.json").write_text(
            json.dumps(pointeur, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # 2. Commit.
        reg.etape_publication(pub, "commit_ecrit", nouveau)
        _git("add", "-A", "New_AI", "training/champions", depot=depot)
        _git("commit", "-q", "-m",
             "scoring: promouvoir %s\n\nCandidat %s, parent %s.\nBundle %s.\n"
             % (nouveau, candidat["id"], champion_attendu, candidat["bundle_sha256"][:16]),
             depot=depot)
        commit_champion = _git("rev-parse", "HEAD", depot=depot).strip()

        # 3. Controle : le bundle commite est-il celui qui a ete mesure ?
        emp = mod_bundle.empreinte(commit_champion)
        if emp["sha256"] != candidat["bundle_sha256"]:
            raise RuntimeError(
                "le bundle du commit de champion (%s) differe de celui evalue (%s). La "
                "promotion est annulee : il faut une nouvelle evaluation."
                % (emp["sha256"][:16], candidat["bundle_sha256"][:16]))

        # 4. Tag annote.
        reg.etape_publication(pub, "tag_ecrit", tag)
        if not tag_existe(tag, depot=depot):
            _git("tag", "-a", tag, "-m",
                 "%s — promu depuis %s\nBundle %s\n" % (nouveau, candidat["id"], emp["sha256"]),
                 depot=depot)

        reg.cloturer_publication(pub, {"id": nouveau, "commit_code": candidat["commit"],
                                       "bundle_sha256": candidat["bundle_sha256"], "tag": tag,
                                       "precedent": champion_attendu,
                                       "manifeste": str(chemin_manifeste)})
        _git("checkout", depart, depot=depot, verifier=False)
        return {"champion": nouveau, "tag": tag, "commit_champion": commit_champion,
                "manifeste": str(chemin_manifeste)}
    except Exception:
        reg.etape_publication(pub, "echec", "voir le journal")
        raise


def reconcilier(reg, champions: Path = CHAMPIONS, depot: Path | None = None) -> dict[str, Any]:
    """Termine ou constate une publication interrompue. N'en cree jamais une seconde.

    La verite est dans GIT, pas dans le journal : le journal dit ou on en etait, Git dit ce qui
    existe. Une reprise qui ferait confiance au seul journal pourrait annoncer un champion dont
    le commit n'a jamais ete ecrit.
    """
    depot = Path(depot or DEPOT)
    en_cours = reg.publication_en_cours()
    if en_cours is None:
        return {"etat": "rien_a_reconcilier"}
    nouveau = en_cours["nouveau_champion"]
    tag = "scoring/%s" % nouveau
    manifeste = Path(champions) / ("%s.json" % nouveau)
    pointeur = json.loads((Path(champions) / "current.json").read_text(encoding="utf-8"))

    a_le_tag = tag_existe(tag, depot=depot)
    pointe_dessus = pointeur.get("champion_courant") == nouveau

    if a_le_tag and pointe_dessus and manifeste.exists():
        commit = _git("rev-list", "-n", "1", tag, depot=depot).strip()
        emp = mod_bundle.empreinte(commit)
        m = json.loads(manifeste.read_text(encoding="utf-8"))
        coherent = emp["sha256"] == m["code"]["bundle_sha256"]
        if coherent:
            reg.cloturer_publication(en_cours["id"],
                                     {"id": nouveau, "commit_code": m["code"]["commit_candidat"],
                                      "bundle_sha256": m["code"]["bundle_sha256"], "tag": tag,
                                      "precedent": en_cours["champion_attendu"],
                                      "manifeste": str(manifeste)})
            return {"etat": "terminee_par_reprise", "champion": nouveau, "tag": tag}
        return {"etat": "incoherent", "champion": nouveau,
                "detail": "le bundle du tag ne correspond pas au manifeste ; ne pas promouvoir."}

    return {"etat": "incomplete", "champion": nouveau, "etape_atteinte": en_cours["etape"],
            "tag_present": a_le_tag, "pointeur_a_jour": pointe_dessus,
            "manifeste_present": manifeste.exists(),
            "detail": ("Publication interrompue. Rien n'est promu : la campagne reste sur son "
                       "champion precedent tant que cette publication n'est pas achevee ou "
                       "abandonnee explicitement.")}

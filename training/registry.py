"""Registre du laboratoire : cache de matchs, candidats, champions et publications.

Deux problemes gouvernent ce module.

**Git et SQLite ne partagent pas de transaction.** Une promotion ecrit un commit, un tag, un
pointeur et une ligne de base. Une interruption au milieu laisserait soit un champion annonce
dont le code est introuvable, soit un commit orphelin qu'une reprise promouvrait une seconde
fois. La parade est un JOURNAL de publication a etats : chaque etape est enregistree avant
d'etre faite, et la reprise lit ce journal pour terminer ou constater, jamais pour refaire.

**Le champion peut avoir change pendant qu'un candidat etait evalue.** La promotion verifie
donc, dans la meme transaction, que le champion attendu est toujours le champion courant. S'il
a bouge, la confirmation est perimee : le candidat n'est pas promu sur une comparaison avec
une ancienne reference.

Le cache de matchs, lui, reste valide pour ses entrees exactes. Changer de champion ne rend
pas faux un combat deja joue ; ce sont les COMPARAISONS qu'il faut reconstruire.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

RACINE = Path(__file__).resolve().parent
RUNS = RACINE / "runs"
CHAMPIONS = RACINE / "champions"

SCHEMA = """
CREATE TABLE IF NOT EXISTS campagnes (
  id TEXT PRIMARY KEY, cree_le TEXT, config_json TEXT, empreinte_config TEXT, statut TEXT);

CREATE TABLE IF NOT EXISTS candidats (
  id TEXT PRIMARY KEY, campagne TEXT, commit_code TEXT, parent TEXT, bundle_sha256 TEXT,
  hypothese TEXT, portee_json TEXT, etat TEXT, cree_le TEXT);

CREATE TABLE IF NOT EXISTS matchs (
  cle TEXT PRIMARY KEY, format TEXT, bloc TEXT, orientation TEXT,
  politique_gauche TEXT, politique_droite TEXT,
  score_gauche REAL, erreur TEXT, detail TEXT,
  duree_tours INTEGER, ms_execution REAL, brut_json TEXT, joue_le TEXT);

CREATE TABLE IF NOT EXISTS blocs (
  campagne TEXT, format TEXT, indice INTEGER, adversaire TEXT, graine INTEGER,
  spec_json TEXT, PRIMARY KEY (campagne, format, indice));

CREATE TABLE IF NOT EXISTS champions (
  id TEXT PRIMARY KEY, commit_code TEXT, bundle_sha256 TEXT, tag TEXT,
  precedent TEXT, promu_le TEXT, manifeste TEXT);

CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, candidat TEXT, champion_attendu TEXT,
  nouveau_champion TEXT, etape TEXT, etat TEXT, detail TEXT, horodatage TEXT);

CREATE TABLE IF NOT EXISTS journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT, horodatage TEXT, niveau TEXT, message TEXT);
"""


class ChampionObsolete(RuntimeError):
    """Le champion a change pendant l'evaluation : la confirmation ne vaut plus."""


class Registre:
    def __init__(self, chemin: Path | None = None):
        self.chemin = Path(chemin or (RUNS / "registre.sqlite"))
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        self.cx = sqlite3.connect(self.chemin, isolation_level=None, timeout=30.0)
        self.cx.row_factory = sqlite3.Row
        # WAL : plusieurs workers lisent le cache pendant qu'un seul ecrit.
        self.cx.execute("PRAGMA journal_mode=WAL")
        self.cx.execute("PRAGMA synchronous=FULL")
        self.cx.executescript(SCHEMA)

    def fermer(self) -> None:
        """Windows verrouille le fichier tant que la connexion vit : un test qui detruit son
        repertoire temporaire sans fermer echoue au menage, pas sur la logique."""
        try:
            self.cx.close()
        except Exception:
            pass

    def __enter__(self) -> "Registre":
        return self

    def __exit__(self, *exc) -> None:
        self.fermer()

    # ---- utilitaires ----------------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.cx.execute("BEGIN IMMEDIATE")
        try:
            yield self.cx
            self.cx.execute("COMMIT")
        except BaseException:
            self.cx.execute("ROLLBACK")
            raise

    def note(self, niveau: str, message: str) -> None:
        self.cx.execute("INSERT INTO journal (horodatage, niveau, message) VALUES (?,?,?)",
                        (_maintenant(), niveau, message))

    # ---- cache de matchs ------------------------------------------------------------
    def match_connu(self, cle: str) -> sqlite3.Row | None:
        return self.cx.execute("SELECT * FROM matchs WHERE cle=?", (cle,)).fetchone()

    def enregistrer_match(self, cle: str, format_nom: str, bloc: str, orientation: str,
                          gauche: str, droite: str, score_gauche: float | None,
                          erreur: str, detail: str, duree_tours: int | None,
                          ms: float | None, brut: dict[str, Any]) -> None:
        """Ecriture ATOMIQUE apres chaque combat termine : une interruption ne perd que le
        combat en cours, jamais le lot."""
        self.cx.execute(
            "INSERT OR REPLACE INTO matchs (cle, format, bloc, orientation, politique_gauche,"
            " politique_droite, score_gauche, erreur, detail, duree_tours, ms_execution,"
            " brut_json, joue_le) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cle, format_nom, bloc, orientation, gauche, droite, score_gauche, erreur, detail,
             duree_tours, ms, json.dumps(brut, ensure_ascii=False), _maintenant()))

    # ---- champions ------------------------------------------------------------------
    def champion_courant(self) -> dict[str, Any]:
        pointeur = CHAMPIONS / "current.json"
        if not pointeur.exists():
            raise FileNotFoundError("pointeur de champion absent : %s" % pointeur)
        return json.loads(pointeur.read_text(encoding="utf-8"))

    def ouvrir_publication(self, candidat: str, champion_attendu: str,
                           nouveau: str) -> int:
        """Ouvre le journal de publication AVANT toute ecriture Git.

        La verification du champion attendu se fait ici, dans la meme transaction que
        l'ouverture : deux promotions concurrentes ne peuvent pas ouvrir sur le meme
        predecesseur.
        """
        with self.transaction() as cx:
            courant = self.champion_courant()["champion_courant"]
            if courant != champion_attendu:
                raise ChampionObsolete(
                    "le champion est passe de %s a %s pendant l'evaluation de %s : la "
                    "confirmation ne vaut plus." % (champion_attendu, courant, candidat))
            en_cours = cx.execute(
                "SELECT id, nouveau_champion FROM publications WHERE etat='en_cours'").fetchone()
            if en_cours is not None:
                raise RuntimeError(
                    "une publication est deja en cours (%s). La reprise doit la terminer avant "
                    "d'en ouvrir une autre." % en_cours["nouveau_champion"])
            cur = cx.execute(
                "INSERT INTO publications (candidat, champion_attendu, nouveau_champion, etape,"
                " etat, detail, horodatage) VALUES (?,?,?,?,?,?,?)",
                (candidat, champion_attendu, nouveau, "ouverte", "en_cours", "", _maintenant()))
            return int(cur.lastrowid)

    def etape_publication(self, pub_id: int, etape: str, detail: str = "") -> None:
        """Chaque etape est notee AVANT d'etre faite : une interruption laisse une trace de ce
        qui restait a faire, pas un silence."""
        self.cx.execute("UPDATE publications SET etape=?, detail=?, horodatage=? WHERE id=?",
                        (etape, detail, _maintenant(), pub_id))

    def cloturer_publication(self, pub_id: int, champion: dict[str, Any]) -> None:
        with self.transaction() as cx:
            cx.execute("INSERT OR REPLACE INTO champions (id, commit_code, bundle_sha256, tag,"
                       " precedent, promu_le, manifeste) VALUES (?,?,?,?,?,?,?)",
                       (champion["id"], champion["commit_code"], champion["bundle_sha256"],
                        champion["tag"], champion.get("precedent"), _maintenant(),
                        champion.get("manifeste", "")))
            cx.execute("UPDATE publications SET etape='terminee', etat='terminee', horodatage=?"
                       " WHERE id=?", (_maintenant(), pub_id))

    def publication_en_cours(self) -> sqlite3.Row | None:
        return self.cx.execute(
            "SELECT * FROM publications WHERE etat='en_cours' ORDER BY id DESC LIMIT 1").fetchone()

    # ---- candidats ------------------------------------------------------------------
    def enregistrer_candidat(self, ident: str, campagne: str, commit_code: str, parent: str,
                             bundle: str, hypothese: str, portee: dict[str, Any]) -> None:
        self.cx.execute(
            "INSERT OR REPLACE INTO candidats (id, campagne, commit_code, parent, bundle_sha256,"
            " hypothese, portee_json, etat, cree_le) VALUES (?,?,?,?,?,?,?,?,?)",
            (ident, campagne, commit_code, parent, bundle, hypothese,
             json.dumps(portee, ensure_ascii=False), "enregistre", _maintenant()))

    def candidat(self, ident: str) -> sqlite3.Row | None:
        return self.cx.execute("SELECT * FROM candidats WHERE id=?", (ident,)).fetchone()

    def bundle_deja_evalue(self, bundle: str) -> sqlite3.Row | None:
        """Un bundle identique a un candidat deja evalue ne merite pas d'etre rejoue."""
        return self.cx.execute(
            "SELECT * FROM candidats WHERE bundle_sha256=? ORDER BY cree_le LIMIT 1",
            (bundle,)).fetchone()


def _maintenant() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")

"""Registre du laboratoire : cache de matchs, candidats, champions, confirmations, publications.

Trois problemes gouvernent ce module.

**Git et SQLite ne partagent pas de transaction.** Une promotion ecrit un commit, un tag, un
pointeur et une ligne de base. Une interruption au milieu laisserait soit un champion annonce
dont le code est introuvable, soit un commit orphelin qu'une reprise promouvrait une seconde
fois. La parade est un JOURNAL de publication a etats : chaque etape est enregistree avant
d'etre faite, et la reprise lit ce journal pour terminer ou constater, jamais pour refaire.
Le pointeur du champion actif est ecrit EN DERNIER, et tant que la publication n'est pas
close, `champion_courant` rend le champion PRECEDENT, conserve dans la ligne de publication.
Une coupure ne peut donc pas faire lire un champion qui n'est pas encore publie.

**Le champion peut avoir change pendant qu'un candidat etait evalue.** La promotion verifie
donc, dans la meme transaction, que le champion attendu est toujours le champion courant. S'il
a bouge, la confirmation est perimee : le candidat n'est pas promu sur une comparaison avec
une ancienne reference.

**Une confirmation est une TENTATIVE nommee, pas un simple appel.** Elle fige son plan de
graines, le champion compare, ses tailles et l'empreinte du protocole. La reprise d'une
tentative rejoue exactement le meme plan ; une nouvelle tentative recoit une vague de graines
neuve, et le budget de campagne les compte. Sans cela, deux confirmations successives d'une
meme idee retombaient sur les memes 160 blocs, et « reconfirmer » ne confirmait plus rien.

Le cache de matchs, lui, reste valide pour ses entrees exactes. Changer de champion ne rend
pas faux un combat deja joue ; ce sont les COMPARAISONS qu'il faut reconstruire.
"""
from __future__ import annotations

import json
import sqlite3
import threading
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

CREATE TABLE IF NOT EXISTS confirmations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, campagne TEXT, candidat TEXT, tentative INTEGER,
  vague TEXT UNIQUE, champion_attendu TEXT, tailles_json TEXT, adversaires_json TEXT,
  empreinte_protocole TEXT, etat TEXT, verdict TEXT, ouverte_le TEXT, close_le TEXT);

CREATE TABLE IF NOT EXISTS avancement (
  campagne TEXT, candidat TEXT, etape TEXT, verdict TEXT, objectif REAL, complet INTEGER,
  rapport TEXT, horodatage TEXT, PRIMARY KEY (campagne, candidat, etape));

CREATE TABLE IF NOT EXISTS journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT, horodatage TEXT, niveau TEXT, message TEXT);
"""

# Colonnes ajoutees apres la premiere version du schema. Une base existante les recoit au
# prochain ouverture ; recreer la base perdrait le cache de matchs, donc on migre.
COLONNES_AJOUTEES = {
    "publications": [("pointeur_precedent", "TEXT"), ("commit_champion", "TEXT"),
                     ("confirmation", "INTEGER")],
    "campagnes": [("empreinte_protocole", "TEXT"), ("protocole_json", "TEXT")],
}


class ChampionObsolete(RuntimeError):
    """Le champion a change pendant l'evaluation : la confirmation ne vaut plus."""


class BudgetEpuise(RuntimeError):
    """Le plafond de confirmations de la campagne est atteint."""


class ProtocoleDifferent(RuntimeError):
    """Le protocole enregistre ne correspond plus a l'environnement courant."""


# Etats d'une tentative de confirmation.
#   ouverte    : lancee, resultat pas encore rendu
#   partielle  : resultat INCOMPLET — le plan est paye a moitie, il se REPREND
#   close      : une decision a ete rendue sur le plan entier
#   abandonnee : renoncement explicite ; ne se reprend pas
ETATS_REPRENABLES = ("ouverte", "partielle")
ETATS_TERMINAUX = ("close", "abandonnee")


class Registre:
    def __init__(self, chemin: Path | None = None, champions: Path | None = None):
        self.chemin = Path(chemin or (RUNS / "registre.sqlite"))
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        self.champions = Path(champions or CHAMPIONS)
        # Plusieurs workers enregistrent leurs combats des qu'ils finissent : la connexion est
        # partagee entre threads et serialisee par un verrou. SQLite refuse par defaut une
        # connexion utilisee hors de son thread de creation.
        self.cx = sqlite3.connect(self.chemin, isolation_level=None, timeout=30.0,
                                  check_same_thread=False)
        self.cx.row_factory = sqlite3.Row
        self.verrou = threading.RLock()
        # WAL : plusieurs workers lisent le cache pendant qu'un seul ecrit.
        self.cx.execute("PRAGMA journal_mode=WAL")
        self.cx.execute("PRAGMA synchronous=FULL")
        self.cx.executescript(SCHEMA)
        self._migrer()

    def _migrer(self) -> None:
        for table, colonnes in COLONNES_AJOUTEES.items():
            presentes = {r["name"] for r in
                         self.cx.execute("PRAGMA table_info(%s)" % table).fetchall()}
            for nom, type_sql in colonnes:
                if nom not in presentes:
                    self.cx.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, nom, type_sql))

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
    def executer(self, sql: str, parametres: tuple = ()) -> sqlite3.Cursor:
        with self.verrou:
            return self.cx.execute(sql, parametres)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.verrou:
            self.cx.execute("BEGIN IMMEDIATE")
            try:
                yield self.cx
                self.cx.execute("COMMIT")
            except BaseException:
                self.cx.execute("ROLLBACK")
                raise

    def note(self, niveau: str, message: str) -> None:
        self.executer("INSERT INTO journal (horodatage, niveau, message) VALUES (?,?,?)",
                      (_maintenant(), niveau, message))

    def journal(self, limite: int = 50) -> list[sqlite3.Row]:
        return self.executer("SELECT * FROM journal ORDER BY id DESC LIMIT ?",
                             (limite,)).fetchall()

    # ---- cache de matchs ------------------------------------------------------------
    def match_connu(self, cle: str) -> sqlite3.Row | None:
        return self.executer("SELECT * FROM matchs WHERE cle=?", (cle,)).fetchone()

    def enregistrer_match(self, cle: str, format_nom: str, bloc: str, orientation: str,
                          gauche: str, droite: str, score_gauche: float | None,
                          erreur: str, detail: str, duree_tours: int | None,
                          ms: float | None, brut: dict[str, Any]) -> None:
        """Ecriture ATOMIQUE apres chaque combat termine : une interruption ne perd que le
        combat en cours, jamais le lot."""
        self.executer(
            "INSERT OR REPLACE INTO matchs (cle, format, bloc, orientation, politique_gauche,"
            " politique_droite, score_gauche, erreur, detail, duree_tours, ms_execution,"
            " brut_json, joue_le) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cle, format_nom, bloc, orientation, gauche, droite, score_gauche, erreur, detail,
             duree_tours, ms, json.dumps(brut, ensure_ascii=False), _maintenant()))

    # ---- campagnes ------------------------------------------------------------------
    def campagne(self, ident: str) -> sqlite3.Row | None:
        return self.executer("SELECT * FROM campagnes WHERE id=?", (ident,)).fetchone()

    def enregistrer_campagne(self, ident: str, config_json: str, empreinte_config: str,
                             empreinte_protocole: str, protocole: dict[str, Any]) -> str:
        """Cree la campagne, ou constate qu'elle existe A L'IDENTIQUE.

        Un protocole different sous le meme identifiant est REFUSE : reecrire en silence une
        campagne dont on a deja lu des resultats changerait le sens de ces resultats. La
        nouvelle idee est une nouvelle campagne, avec son propre identifiant et son budget.
        """
        with self.transaction() as cx:
            existante = cx.execute("SELECT * FROM campagnes WHERE id=?", (ident,)).fetchone()
            if existante is not None:
                if (existante["empreinte_protocole"] or "") == empreinte_protocole:
                    return "inchangee"
                raise ProtocoleDifferent(
                    "la campagne %s existe avec un autre protocole (%s != %s). Un changement "
                    "de protocole demande une NOUVELLE campagne : changer campagne.id."
                    % (ident, (existante["empreinte_protocole"] or "?")[:12],
                       empreinte_protocole[:12]))
            cx.execute("INSERT INTO campagnes (id, cree_le, config_json, empreinte_config,"
                       " statut, empreinte_protocole, protocole_json) VALUES (?,?,?,?,?,?,?)",
                       (ident, _maintenant(), config_json, empreinte_config, "preparee",
                        empreinte_protocole,
                        json.dumps(protocole, ensure_ascii=False, sort_keys=True)))
            return "creee"

    def verifier_protocole(self, ident: str, empreinte_protocole: str) -> sqlite3.Row:
        """Le protocole enregistre est-il toujours celui de l'environnement courant ?"""
        ligne = self.campagne(ident)
        if ligne is None:
            raise ProtocoleDifferent(
                "campagne %s inconnue du registre : lancer init-campaign avant d'evaluer."
                % ident)
        enregistre = ligne["empreinte_protocole"] or ""
        if enregistre != empreinte_protocole:
            raise ProtocoleDifferent(
                "le protocole a change depuis init-campaign (%s enregistre, %s courant). "
                "Moteur, runner, builds, ligue ou configuration ont bouge : les resultats deja "
                "obtenus ne sont plus comparables. Ouvrir une nouvelle campagne."
                % (enregistre[:12] or "?", empreinte_protocole[:12]))
        return ligne

    # ---- avancement d'une vague ------------------------------------------------------
    def enregistrer_avancement(self, campagne: str, candidat: str, etape: str, verdict: str,
                               objectif: float | None, complet: bool,
                               rapport: dict[str, Any]) -> None:
        """L'etape franchie par un candidat, avec son rapport COMPLET.

        Sans cette trace, une boucle relancee apres une interruption ne savait plus ou en
        etait un candidat : elle tentait de le reenregistrer, se heurtait a sa branche, et
        laissait sur place des combats deja payes que plus rien ne reliait a une idee.
        """
        self.executer(
            "INSERT OR REPLACE INTO avancement (campagne, candidat, etape, verdict, objectif,"
            " complet, rapport, horodatage) VALUES (?,?,?,?,?,?,?,?)",
            (campagne, candidat, etape, verdict, objectif, 1 if complet else 0,
             json.dumps(rapport, ensure_ascii=False), _maintenant()))

    def avancement(self, campagne: str, candidat: str) -> dict[str, sqlite3.Row]:
        return {r["etape"]: r for r in self.executer(
            "SELECT * FROM avancement WHERE campagne=? AND candidat=?",
            (campagne, candidat)).fetchall()}

    def oublier_avancement(self, campagne: str, candidat: str, etape: str) -> None:
        self.executer("DELETE FROM avancement WHERE campagne=? AND candidat=? AND etape=?",
                      (campagne, candidat, etape))

    # ---- confirmations --------------------------------------------------------------
    def confirmation_reprenable(self, campagne: str, candidat: str) -> sqlite3.Row | None:
        """La tentative a COMPLETER, s'il y en a une. Une tentative close ou abandonnee n'en
        est pas une."""
        marques = ",".join("?" * len(ETATS_REPRENABLES))
        return self.executer(
            "SELECT * FROM confirmations WHERE campagne=? AND candidat=? AND etat IN (%s)"
            " ORDER BY id DESC LIMIT 1" % marques,
            (campagne, candidat, *ETATS_REPRENABLES)).fetchone()

    def confirmations_utilisees(self, campagne: str) -> int:
        return int(self.executer("SELECT COUNT(*) AS n FROM confirmations WHERE campagne=?",
                                 (campagne,)).fetchone()["n"])

    def ouvrir_confirmation(self, campagne: str, candidat: str, champion_attendu: str,
                            tailles: dict[str, int], adversaires: list[str],
                            empreinte_protocole: str,
                            plafond: int) -> tuple[sqlite3.Row, bool]:
        """(tentative, reprise). Une tentative INACHEVEE est reprise avec son plan de graines.

        Une tentative neuve recoit une vague inedite, verifiee unique en base : c'est ce qui
        garantit qu'une idee retouchee apres avoir vu ses resultats est reconfirmee sur un
        echantillon nouveau, et non sur celui qui a servi a la retoucher.

        Mais une tentative simplement COUPEE n'est pas une idee retouchee : elle doit reprendre
        le meme plan, sans consommer une unite de plus. Ne reprendre que les lignes `ouverte`
        faisait ouvrir une seconde tentative apres chaque coupure — nouvelles graines, budget
        entame, plan deja paye jamais termine.

        Le champion fige dans la tentative est CONTROLE a la reprise : une comparaison devenue
        obsolete ne doit pas changer de reference en silence.
        """
        with self.transaction() as cx:
            marques = ",".join("?" * len(ETATS_REPRENABLES))
            ouverte = cx.execute(
                "SELECT * FROM confirmations WHERE campagne=? AND candidat=? AND etat IN (%s)"
                " ORDER BY id DESC LIMIT 1" % marques,
                (campagne, candidat, *ETATS_REPRENABLES)).fetchone()
            if ouverte is not None:
                if (ouverte["empreinte_protocole"] or "") != empreinte_protocole:
                    raise ProtocoleDifferent(
                        "la tentative %s a ete ouverte sous un autre protocole : elle ne peut "
                        "pas etre reprise telle quelle." % ouverte["vague"])
                if ouverte["champion_attendu"] != champion_attendu:
                    raise ChampionObsolete(
                        "la tentative %s compare a %s, mais le champion est %s : reprendre ce "
                        "plan mesurerait deux references differentes. L'abandonner "
                        "explicitement, puis en ouvrir une neuve."
                        % (ouverte["vague"], ouverte["champion_attendu"], champion_attendu))
                return ouverte, True
            utilisees = int(cx.execute(
                "SELECT COUNT(*) AS n FROM confirmations WHERE campagne=?",
                (campagne,)).fetchone()["n"])
            if utilisees >= plafond:
                raise BudgetEpuise(
                    "budget de confirmations epuise pour %s : %d tentatives sur %d. Le risque "
                    "nominal annonce vaut pour CE budget ; l'etendre demande une nouvelle "
                    "campagne et un nouveau choix explicite." % (campagne, utilisees, plafond))
            derniere = cx.execute(
                "SELECT MAX(tentative) AS t FROM confirmations WHERE campagne=? AND candidat=?",
                (campagne, candidat)).fetchone()["t"]
            tentative = int(derniere or 0) + 1
            vague = "confirm-%s-%s-%02d" % (campagne, candidat, tentative)
            if cx.execute("SELECT 1 FROM confirmations WHERE vague=?", (vague,)).fetchone():
                raise RuntimeError("vague de confirmation deja utilisee : %s" % vague)
            cur = cx.execute(
                "INSERT INTO confirmations (campagne, candidat, tentative, vague,"
                " champion_attendu, tailles_json, adversaires_json, empreinte_protocole, etat,"
                " verdict, ouverte_le, close_le) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (campagne, candidat, tentative, vague, champion_attendu,
                 json.dumps(tailles, sort_keys=True), json.dumps(adversaires),
                 empreinte_protocole, "ouverte", "", _maintenant(), ""))
            ligne = cx.execute("SELECT * FROM confirmations WHERE id=?",
                               (cur.lastrowid,)).fetchone()
            return ligne, False

    def cloturer_confirmation(self, ident: int, etat: str, verdict: str) -> None:
        """`close` quand une decision a porte sur le plan ENTIER, `partielle` quand il reste
        des combats a jouer, `abandonnee` pour un renoncement explicite. Seul `partielle` se
        reprend, et une reprise ne consomme aucune unite de budget."""
        if etat not in ETATS_REPRENABLES + ETATS_TERMINAUX:
            raise ValueError("etat de confirmation inconnu : %r" % etat)
        self.executer("UPDATE confirmations SET etat=?, verdict=?, close_le=? WHERE id=?",
                      (etat, verdict, _maintenant(), ident))

    def confirmation(self, ident: int) -> sqlite3.Row | None:
        return self.executer("SELECT * FROM confirmations WHERE id=?", (ident,)).fetchone()

    # ---- champions ------------------------------------------------------------------
    def pointeur_fichier(self) -> dict[str, Any]:
        pointeur = self.champions / "current.json"
        if not pointeur.exists():
            raise FileNotFoundError("pointeur de champion absent : %s" % pointeur)
        return json.loads(pointeur.read_text(encoding="utf-8"))

    def champion_courant(self) -> dict[str, Any]:
        """Le champion ACTIF, c'est-a-dire celui dont la publication est CLOSE.

        Si une publication est encore en cours et que son pointeur a deja ete ecrit, ce
        pointeur ne fait pas foi : la reference conservee a l'ouverture est rendue a sa place.
        Sans cela, une coupure entre l'ecriture du pointeur et la cloture faisait annoncer un
        champion que rien ne garantissait publie.
        """
        pointeur = self.pointeur_fichier()
        en_cours = self.publication_en_cours()
        if en_cours is not None and pointeur.get("champion_courant") == en_cours["nouveau_champion"]:
            precedent = en_cours["pointeur_precedent"]
            if precedent:
                return json.loads(precedent)
        return pointeur

    def champion_enregistre(self, ident: str) -> sqlite3.Row | None:
        return self.executer("SELECT * FROM champions WHERE id=?", (ident,)).fetchone()

    def ouvrir_publication(self, candidat: str, champion_attendu: str, nouveau: str,
                           confirmation: int | None = None) -> int:
        """Ouvre le journal de publication AVANT toute ecriture Git.

        La verification du champion attendu se fait ici, dans la meme transaction que
        l'ouverture : deux promotions concurrentes ne peuvent pas ouvrir sur le meme
        predecesseur. Le pointeur actuel est recopie dans la ligne : c'est lui qui sera rendu
        comme champion actif tant que la publication n'est pas close, et lui qu'une reprise
        restaurera si la publication doit etre abandonnee.
        """
        with self.transaction() as cx:
            pointeur = self.pointeur_fichier()
            courant = pointeur["champion_courant"]
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
                " etat, detail, horodatage, pointeur_precedent, commit_champion, confirmation)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (candidat, champion_attendu, nouveau, "ouverte", "en_cours", "", _maintenant(),
                 json.dumps(pointeur, ensure_ascii=False), "", confirmation))
            return int(cur.lastrowid)

    def etape_publication(self, pub_id: int, etape: str, detail: str = "",
                          commit_champion: str | None = None) -> None:
        """Chaque etape est notee AVANT d'etre faite : une interruption laisse une trace de ce
        qui restait a faire, pas un silence."""
        if commit_champion is None:
            self.executer("UPDATE publications SET etape=?, detail=?, horodatage=? WHERE id=?",
                          (etape, detail, _maintenant(), pub_id))
        else:
            self.executer("UPDATE publications SET etape=?, detail=?, horodatage=?,"
                          " commit_champion=? WHERE id=?",
                          (etape, detail, _maintenant(), commit_champion, pub_id))

    def cloturer_publication(self, pub_id: int, champion: dict[str, Any]) -> None:
        with self.transaction() as cx:
            cx.execute("INSERT OR REPLACE INTO champions (id, commit_code, bundle_sha256, tag,"
                       " precedent, promu_le, manifeste) VALUES (?,?,?,?,?,?,?)",
                       (champion["id"], champion["commit_code"], champion["bundle_sha256"],
                        champion["tag"], champion.get("precedent"), _maintenant(),
                        champion.get("manifeste", "")))
            cx.execute("UPDATE publications SET etape='terminee', etat='terminee', horodatage=?"
                       " WHERE id=?", (_maintenant(), pub_id))

    def abandonner_publication(self, pub_id: int, detail: str) -> None:
        """Une publication que la reprise ne peut pas terminer est close en ABANDON. Le
        champion actif reste le precedent, et le journal dit pourquoi."""
        self.executer("UPDATE publications SET etape='abandonnee', etat='abandonnee', detail=?,"
                      " horodatage=? WHERE id=?", (detail, _maintenant(), pub_id))
        self.note("avertissement", "publication %d abandonnee : %s" % (pub_id, detail))

    def publication_en_cours(self) -> sqlite3.Row | None:
        return self.executer(
            "SELECT * FROM publications WHERE etat='en_cours' ORDER BY id DESC LIMIT 1"
        ).fetchone()

    # ---- candidats ------------------------------------------------------------------
    def enregistrer_candidat(self, ident: str, campagne: str, commit_code: str, parent: str,
                             bundle: str, hypothese: str, portee: dict[str, Any],
                             etat: str = "enregistre") -> None:
        self.executer(
            "INSERT OR REPLACE INTO candidats (id, campagne, commit_code, parent, bundle_sha256,"
            " hypothese, portee_json, etat, cree_le) VALUES (?,?,?,?,?,?,?,?,?)",
            (ident, campagne, commit_code, parent, bundle, hypothese,
             json.dumps(portee, ensure_ascii=False), etat, _maintenant()))

    def etat_candidat(self, ident: str, etat: str) -> None:
        self.executer("UPDATE candidats SET etat=? WHERE id=?", (etat, ident))

    def candidat(self, ident: str) -> sqlite3.Row | None:
        return self.executer("SELECT * FROM candidats WHERE id=?", (ident,)).fetchone()

    def bundle_deja_evalue(self, bundle: str) -> sqlite3.Row | None:
        """Un bundle identique a un candidat deja evalue ne merite pas d'etre rejoue."""
        return self.executer(
            "SELECT * FROM candidats WHERE bundle_sha256=? ORDER BY cree_le LIMIT 1",
            (bundle,)).fetchone()


def _maintenant() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")

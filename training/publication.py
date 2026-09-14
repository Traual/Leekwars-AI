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

    ouverte -> code_prepare -> bundle_verifie -> commit_ecrit -> commit_enregistre
            -> tag_ecrit -> pointeur_ecrit -> terminee

Le journal ne suffit jamais a lui seul, parce qu'une coupure tombe aussi ENTRE une ecriture Git
reussie et la ligne SQLite qui la note. La reprise cherche donc le commit de champion par trois
faits successifs — le tag, le SHA enregistre, puis le manifeste versionne sur la branche — et
lit le pointeur tel qu'il est COMMITE, pas tel qu'il traine dans l'arbre de travail.

La reprise aboutit toujours a l'un des deux etats surs : la publication est terminee et
controlee — tag, manifeste versionne, pointeur commite, arbre propre — ou le champion
precedent est reellement conserve, pointeur restaure, arbre nettoye, manifeste orphelin retire.
Le controle qui decide est le meme dans les deux cas : l'arbre `New_AI` du commit de champion
doit etre EXACTEMENT celui qui a ete evalue, suppressions comprises.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import bundle as mod_bundle

DEPOT = Path(__file__).resolve().parents[1]
CHAMPIONS = Path(__file__).resolve().parent / "champions"
BRANCHE = "scoring"

# La ZONE de publication : les seuls chemins que cette operation ecrit, et les seuls
# qu'un abandon a le droit de nettoyer. Rien ailleurs dans le depot n'est touche.
ZONE = ("New_AI", "training/champions")

ETAPES = ("ouverte", "code_prepare", "bundle_verifie", "commit_ecrit",
          "commit_enregistre", "tag_ecrit", "pointeur_ecrit", "terminee")

# Points d'arret de RECEPTION. Les cinq premiers tombent AVANT l'operation qu'ils
# nomment ; les deux derniers tombent APRES une ecriture Git reussie et avant la
# suivante — ce sont les fenetres ou le journal et Git divergent, et c'est la que la
# reprise doit se fier aux faits plutot qu'au journal.
COUPURES = ("code_prepare", "bundle_verifie", "commit_ecrit", "apres_commit",
            "tag_ecrit", "pointeur_ecrit", "apres_pointeur")


class CoupureSimulee(RuntimeError):
    """Interruption provoquee par un test de reception, a une frontiere precise."""


class TravauxLocaux(RuntimeError):
    """Des modifications non commitees occupent la zone : la publication refuse de les
    detruire. Rien n'a ete touche, et la decision reste publiable une fois la zone liberee."""


def _git(*args: str, depot: Path | None = None, verifier: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(depot or DEPOT), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
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
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode == 0


def _preparer_sous_arbre(depot: Path, commit: str, sous_arbre: str) -> None:
    """Met dans l'index ET l'arbre de travail EXACTEMENT le sous-arbre du candidat.

    `git checkout <commit> -- New_AI` travaille en SUPERPOSITION : un fichier suivi sur
    `scoring` mais absent du candidat reste en place. Un candidat qui retire un helper de
    scoring voyait donc son helper survivre, l'empreinte de l'arbre prepare differer de celle
    qui avait ete mesuree, et sa promotion refusee — alors que son bundle etait correct.

    On vide donc d'abord l'index et le disque du sous-arbre, puis on le repose depuis le
    candidat. Les suppressions passent ainsi comme le reste.

    Consequence assumee : `New_AI` appartient entierement a l'operation. Un fichier etranger
    qui y trainait ne survit pas — et c'est necessaire, puisqu'un `git add -A` le ferait
    autrement entrer dans le commit de champion et changerait l'arbre publie. C'est pourquoi
    `publier` REFUSE de commencer quand la zone porte du travail non commite : voir
    `travaux_locaux`.
    """
    _git("rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", sous_arbre, depot=depot)
    cible = Path(depot) / sous_arbre
    if cible.exists():
        shutil.rmtree(cible)
    _git("checkout", commit, "--", sous_arbre, depot=depot)


def _ecrire_json(chemin: Path, valeur: dict[str, Any]) -> None:
    """Ecriture ATOMIQUE : une coupure ne doit jamais laisser un pointeur a moitie ecrit,
    qu'aucune reprise ne saurait relire."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    provisoire = chemin.with_name(chemin.name + ".partiel")
    provisoire.write_text(json.dumps(valeur, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    provisoire.replace(chemin)


def _ecrire_pointeur(champions: Path, nouveau: str, candidat: dict[str, Any], tag: str) -> dict:
    pointeur = {"champion_courant": nouveau,
                "manifeste": "training/champions/%s.json" % nouveau,
                "commit_code": candidat["commit"],
                "bundle_sha256": candidat["bundle_sha256"], "tag": tag,
                "_role": "Seul le controleur ecrit ce pointeur ; l'optimiseur n'y touche pas."}
    _ecrire_json(Path(champions) / "current.json", pointeur)
    return pointeur


def pointeur_commite(depot: Path, branche: str = BRANCHE) -> dict[str, Any] | None:
    """Le pointeur tel qu'il est COMMITE sur la branche, pas tel qu'il est sur le disque.

    Une coupure entre l'ecriture du fichier et son commit laisse un pointeur de travail qui
    annonce deja le nouveau champion alors que rien n'est versionne. Une reprise qui lit le
    fichier saute alors le commit qui manque justement.
    """
    brut = _git("show", "%s:training/champions/current.json" % branche,
                depot=depot, verifier=False)
    if not brut.strip():
        return None
    try:
        return json.loads(brut)
    except json.JSONDecodeError:
        return None


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

    # AVANT toute operation destructive, et avant meme d'ouvrir le journal : la preparation
    # vide `New_AI`, donc un travail local non commite y serait perdu sans retour possible.
    try:
        travaux = travaux_locaux(depot)
    except Exception as e:
        # Un inventaire impossible ne vaut JAMAIS un inventaire vide : on refuse, sans rien
        # avoir touche.
        raise TravauxLocaux(
            "inventaire de la zone de publication impossible (%s) ; faute de pouvoir prouver "
            "qu'aucun travail local ne serait detruit, rien n'a ete touche." % e)
    if travaux:
        raise TravauxLocaux(
            "la zone de publication porte des modifications non commitees ; la preparation de "
            "l'arbre du candidat les detruirait sans pouvoir les rendre. Rien n'a ete touche. "
            "Commiter, remiser ou supprimer ces fichiers, puis relancer : la decision reste "
            "publiable. En cause : %s" % " | ".join(travaux[:10]))

    pub = reg.ouvrir_publication(candidat["id"], champion_attendu, nouveau, confirmation)
    depart = _git("rev-parse", "--abbrev-ref", "HEAD", depot=depot).strip()
    try:
        _git("checkout", "-q", BRANCHE, depot=depot)
        # Le changement de branche a pu faire apparaitre des fichiers non suivis propres a
        # `scoring`. Ce sont les seuls que l'abandon preservera.
        reg.etrangers_publication(pub, non_suivis(depot))

        # 1. Le CODE du candidat et son manifeste, dans l'arbre de travail. Le pointeur du
        #    champion actif n'est PAS touche ici.
        reg.etape_publication(pub, "code_prepare", nouveau)
        _peut_etre_coupe("code_prepare")
        _preparer_sous_arbre(depot, candidat["commit"], mod_bundle.SOUS_ARBRE)

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
        _ecrire_json(chemin_manifeste, manifeste)

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

        # 3. Commit du code et du manifeste. La coupure `apres_commit` tombe entre un commit
        #    REUSSI et l'enregistrement de son SHA : la reprise doit alors reconnaitre le
        #    commit par les FAITS Git, pas par le journal.
        reg.etape_publication(pub, "commit_ecrit", nouveau)
        _peut_etre_coupe("commit_ecrit")
        _git("commit", "-q", "-m",
             "scoring: promouvoir %s\n\nCandidat %s, parent %s.\nBundle %s.\n"
             % (nouveau, candidat["id"], champion_attendu, candidat["bundle_sha256"][:16]),
             depot=depot)
        _peut_etre_coupe("apres_commit")
        commit_champion = _git("rev-parse", "HEAD", depot=depot).strip()
        reg.etape_publication(pub, "commit_enregistre", nouveau,
                              commit_champion=commit_champion)

        # 4. Tag annote.
        reg.etape_publication(pub, "tag_ecrit", tag, commit_champion=commit_champion)
        _peut_etre_coupe("tag_ecrit")
        if not tag_existe(tag, depot=depot):
            _git("tag", "-a", tag, "-m",
                 "%s — promu depuis %s\nBundle %s\n" % (nouveau, candidat["id"], emp["sha256"]),
                 depot=depot)

        # 5. SEULEMENT MAINTENANT : le champion actif change. La coupure `apres_pointeur`
        #    tombe entre l'ecriture du fichier et son commit.
        reg.etape_publication(pub, "pointeur_ecrit", tag, commit_champion=commit_champion)
        _peut_etre_coupe("pointeur_ecrit")
        _ecrire_pointeur(champions, nouveau, candidat, tag)
        _git("add", "-A", "training/champions", depot=depot)
        _peut_etre_coupe("apres_pointeur")
        _git("commit", "-q", "-m", "scoring: pointer le champion %s\n" % nouveau, depot=depot)

        reg.cloturer_publication(pub, {"id": nouveau, "commit_code": candidat["commit"],
                                       "bundle_sha256": candidat["bundle_sha256"], "tag": tag,
                                       "precedent": champion_attendu,
                                       "manifeste": str(chemin_manifeste)})
        if confirmation:
            reg.marquer_publication_confirmation(int(confirmation), "publiee", nouveau)
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


def _commit_de_champion(depot: Path, tag: str, enregistre: str, nouveau: str) -> str:
    """Le commit de champion, cherche par les FAITS Git et non par le journal.

    Trois sources, dans l'ordre de certitude : le tag, le SHA enregistre, puis la branche
    elle-meme. La troisieme est indispensable : une coupure entre un commit REUSSI et
    l'enregistrement de son SHA ne laissait ni tag ni SHA, la reprise concluait « aucun commit
    n'existait » et abandonnait — alors que `scoring` portait deja le code du candidat et son
    manifeste, que le nettoyage restaurait ensuite depuis ce meme commit.
    """
    if tag_existe(tag, depot=depot):
        return _git("rev-list", "-n", "1", tag, depot=depot).strip()
    if _commit_existe(enregistre, depot=depot):
        return enregistre.strip()
    # Le manifeste du nouveau champion est-il deja versionne sur la branche ? Si oui, le
    # commit qui l'a introduit EST le commit de champion.
    trouve = _git("rev-list", "-n", "1", BRANCHE, "--",
                  "training/champions/%s.json" % nouveau, depot=depot, verifier=False).strip()
    return trouve


def _controler_publication(depot: Path, champions: Path, nouveau: str, tag: str,
                           bundle: str, etrangers: list[str] | None = None) -> list[str]:
    """Ce qui manquerait encore pour qu'une publication soit vraiment terminee."""
    manques = []
    if not tag_existe(tag, depot=depot):
        manques.append("tag %s absent" % tag)
    else:
        commit = _git("rev-list", "-n", "1", tag, depot=depot).strip()
        if mod_bundle.empreinte(commit)["sha256"] != bundle:
            manques.append("le bundle du tag ne correspond pas au manifeste")
    if not (Path(champions) / ("%s.json" % nouveau)).exists():
        manques.append("manifeste %s absent du disque" % nouveau)
    if not _git("ls-tree", "--name-only", BRANCHE, "training/champions/%s.json" % nouveau,
                depot=depot, verifier=False).strip():
        manques.append("manifeste %s non versionne sur %s" % (nouveau, BRANCHE))
    pointeur = pointeur_commite(depot)
    if (pointeur or {}).get("champion_courant") != nouveau:
        manques.append("le pointeur commite ne designe pas %s" % nouveau)
    sale = _git("status", "--porcelain", "--", "New_AI", "training/champions",
                depot=depot, verifier=False).strip()
    if sale:
        manques.append("arbre de travail non propre : %s" % sale.replace("\n", " | ")[:200])
    return manques


def non_suivis(depot: Path) -> list[str]:
    """Les fichiers NON SUIVIS de la zone de publication, un par ligne."""
    sortie = _git("ls-files", "--others", "--exclude-standard", "--", *ZONE,
                  depot=depot, verifier=False).strip()
    return sorted(l.strip() for l in sortie.split("\n") if l.strip())


def travaux_locaux(depot: Path) -> list[str]:
    """Tout ce qui, dans la zone de publication, n'appartient pas a un commit.

    Fichiers non suivis ET modifications de fichiers suivis. La publication prepare l'arbre du
    candidat en VIDANT `New_AI` : elle detruirait un prototype local cree pendant une
    confirmation, bien apres les controles faits a l'inscription du candidat. Et la liste des
    etrangers ne memorise que des chemins, jamais des contenus : aucun nettoyage ne pourrait
    les rendre.

    Un arbre propre a l'inscription ne l'est pas forcement a la promotion, des heures plus
    tard. On le RECONSTATE donc juste avant, et on refuse plutot que d'effacer.

    **L'inventaire lit le DISQUE, pas `git status`.** `shutil.rmtree` efface tout ce qui existe
    sous `New_AI`, alors que `git status` n'en montre qu'une partie : il tait les fichiers
    ignores (`.gitignore`, `.git/info/exclude`, `core.excludesFile`) et tous les non-suivis
    quand `status.showUntrackedFiles=no`. Un prototype ainsi masque echappait au controle, puis
    disparaissait avec une publication reussie. On parcourt donc chaque fichier reellement
    present dans la zone et on le compare a l'arbre de HEAD :

    - present sur le disque, absent de HEAD : travail local, quelles que soient les regles
      d'exclusion ou d'affichage ;
    - present des deux cotes : son contenu, filtre comme Git le filtre (fins de ligne), doit
      donner exactement le blob de HEAD ;
    - dans HEAD mais absent du disque : suppression locale non commitee.

    Toute impossibilite d'inventorier LEVE : l'appelant refuse. Un inventaire rate ne doit
    jamais valoir un inventaire vide.
    """
    racine = Path(depot)

    # 1. Ce que HEAD suit dans la zone. `-z` : chemins bruts, jamais quotes.
    suivis: dict[str, tuple[str, str]] = {}
    head_brut: dict[str, tuple[str, str]] = {}
    brut = _git("ls-tree", "-r", "-z", "--full-tree", "HEAD", "--", *ZONE, depot=depot)
    for entree in brut.split("\0"):
        if not entree:
            continue
        meta, chemin = entree.split("\t", 1)
        mode, type_objet, objet = meta.split()
        suivis[chemin] = (mode, objet if type_objet == "blob" else "")
        head_brut[chemin] = (mode, objet)

    # 2. Ce qui existe REELLEMENT sur le disque, sans aucune regle d'exclusion.
    presents: list[str] = []
    liens: list[str] = []
    for base in ZONE:
        dossier = racine / base
        if dossier.is_symlink():
            liens.append(base)
            continue
        if not dossier.exists():
            continue
        for p in dossier.rglob("*"):
            relatif = p.relative_to(racine).as_posix()
            if p.is_symlink():
                liens.append(relatif)
            elif p.is_file():
                presents.append(relatif)
            elif not p.is_dir():
                # Ni fichier, ni repertoire, ni lien : on ne sait pas ce que rmtree en ferait.
                raise RuntimeError("entree de nature inconnue dans la zone : %s" % relatif)

    travaux = ["?? %s" % c for c in sorted(presents) if c not in suivis]
    # Un lien symbolique ne se compare pas comme un fichier ; plutot que de deviner, il compte
    # comme un travail local et fait refuser.
    travaux += ["?L %s" % c for c in sorted(liens)]

    # 3. Les fichiers suivis : le contenu present donne-t-il le blob de HEAD ?
    a_verifier = sorted(c for c in presents if c in suivis)
    if a_verifier:
        # `hash-object` sans `-w` n'ecrit rien, et applique les filtres de nettoyage : c'est la
        # definition meme de « modifie » pour Git, independante de tout reglage d'affichage.
        r = subprocess.run(["git", "-C", str(depot), "hash-object", "--stdin-paths"],
                           input="\n".join(a_verifier) + "\n", capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError("git hash-object : %s" % (r.stderr or r.stdout).strip())
        empreintes = r.stdout.split()
        if len(empreintes) != len(a_verifier):
            raise RuntimeError("inventaire incomplet : %d empreintes pour %d fichiers"
                               % (len(empreintes), len(a_verifier)))
        for chemin, empreinte in zip(a_verifier, empreintes):
            if empreinte != suivis[chemin][1]:
                travaux.append(" M %s" % chemin)

    # 4. Suivis dans HEAD mais absents du disque.
    travaux += [" D %s" % c for c in sorted(suivis)
                if not (racine / c).exists() and not (racine / c).is_symlink()]

    # 5. L'INDEX contre HEAD. Le disque ne dit pas tout : une modification indexee puis
    #    retablie sur le disque (etat MM), ou un ajout indexe puis retire du disque (etat AD),
    #    passaient les comparaisons precedentes. La preparation, puis la reprise, reinitialisent
    #    l'index et perdaient ce travail prepare. On lit l'index brut (`ls-files -s`), pas un
    #    diff : aucun reglage de rendu ni de detection de renommage n'intervient.
    indexe: dict[str, tuple[str, str]] = {}
    en_conflit: set[str] = set()
    brut_index = _git("ls-files", "-s", "-z", "--", *ZONE, depot=depot)
    for entree in brut_index.split("\0"):
        if not entree:
            continue
        meta, chemin = entree.split("\t", 1)
        mode, objet, etage = meta.split()
        if etage != "0":
            en_conflit.add(chemin)
        else:
            indexe[chemin] = (mode, objet)
    travaux += ["U  %s" % c for c in sorted(en_conflit)]
    for chemin in sorted((set(indexe) | set(head_brut)) - en_conflit):
        dans_index, dans_head = indexe.get(chemin), head_brut.get(chemin)
        if dans_head is None:
            travaux.append("A  %s" % chemin)
        elif dans_index is None:
            travaux.append("D  %s" % chemin)
        elif dans_index[1] != dans_head[1]:
            travaux.append("M  %s" % chemin)
        elif dans_index[0] != dans_head[0]:
            travaux.append("T  %s" % chemin)
    return travaux


def _nettoyer_arbre(champions: Path, depot: Path, nouveau: str,
                    pointeur_precedent: dict[str, Any] | None,
                    etrangers: list[str] | None = None) -> tuple[list[str], list[str]]:
    """Remet la zone de publication dans l'etat d'avant. Rend (ce qui a ete fait, ce qui reste).

    Le `reset` puis le `checkout` restaurent les fichiers SUIVIS, mais un fichier AJOUTE par le
    candidat redevient simplement non suivi : le checkout ne l'efface pas. Un candidat qui
    ajoutait un helper laissait donc le depot sale apres un abandon, ce qui bloquait ensuite
    l'inscription de tout nouveau candidat.

    On retire donc les non-suivis de la zone que la publication a CREES, c'est-a-dire ceux qui
    n'existaient pas a son ouverture. `etrangers` porte cette liste d'avant ; ces fichiers-la
    sont preserves. Aucun autre endroit du depot n'est touche.
    """
    faits = []
    etrangers = set(etrangers or [])
    sale = _git("status", "--porcelain", "--", *ZONE, depot=depot, verifier=False).strip()
    if sale:
        _git("reset", "-q", "HEAD", "--", *ZONE, depot=depot, verifier=False)
        _git("checkout", "-q", "--", *ZONE, depot=depot, verifier=False)
        faits.append("fichiers suivis restaures")
    crees = [c for c in non_suivis(depot) if c not in etrangers]
    for relatif in crees:
        cible = Path(depot) / relatif
        if cible.is_file():
            cible.unlink()
            faits.append("fichier cree par la publication retire : %s" % relatif)
    if pointeur_precedent:
        actuel = Path(champions) / "current.json"
        if not actuel.exists() or json.loads(actuel.read_text(encoding="utf-8")) \
                .get("champion_courant") != pointeur_precedent.get("champion_courant"):
            _ecrire_json(actuel, pointeur_precedent)
            faits.append("pointeur restaure sur %s" % pointeur_precedent.get("champion_courant"))

    # La proprete est CONSTATEE, pas supposee : un abandon annonce termine doit laisser un
    # depot ou l'inscription du candidat suivant est possible.
    reste = [l.strip() for l in
             _git("status", "--porcelain", "--", *ZONE, depot=depot, verifier=False)
             .strip().split("\n") if l.strip()]
    reste = [l for l in reste if l.split(" ", 1)[-1].strip().strip('"') not in etrangers]
    return faits, reste


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
    etrangers = json.loads(en_cours["non_suivis_json"] or "[]")
    depart = _git("rev-parse", "--abbrev-ref", "HEAD", depot=depot).strip()
    _git("checkout", "-q", BRANCHE, depot=depot, verifier=False)
    try:
        commit = _commit_de_champion(depot, tag, en_cours["commit_champion"] or "", nouveau)

        if not commit:
            faits, reste = _nettoyer_arbre(champions, depot, nouveau, precedent, etrangers)
            reg.abandonner_publication(
                en_cours["id"],
                "interrompue avant le commit de champion (etape %s)" % en_cours["etape"])
            # INTERRUPTION TECHNIQUE, pas renoncement : la decision de confirmation reste
            # valable et sa promotion sera terminee au prochain passage.
            return {"etat": "abandonnee", "recuperable": True,
                    "champion_actif": reg.champion_courant()["champion_courant"],
                    "champion_abandonne": nouveau,
                    "etape_atteinte": en_cours["etape"], "remises_en_etat": faits,
                    "reste_a_nettoyer": reste,
                    "detail": ("Rien n'a ete promu : aucun commit de champion n'existait. Le "
                               "champion actif est reellement le precedent, et la decision de "
                               "confirmation reste publiable.")}

        emp = mod_bundle.empreinte(commit)
        brut = _git("show", "%s:training/champions/%s.json" % (commit, nouveau),
                    depot=depot, verifier=False)
        manifeste = json.loads(brut) if brut.strip() else None
        attendu = (manifeste or {}).get("code", {}).get("bundle_sha256")
        if manifeste is None or emp["sha256"] != attendu:
            faits, reste = _nettoyer_arbre(champions, depot, nouveau, precedent, etrangers)
            reg.abandonner_publication(
                en_cours["id"], "bundle du commit %s incoherent avec le manifeste" % commit[:12])
            # DEFINITIF : le code publie ne correspond pas a ce qui a ete mesure, donc la
            # decision n'est plus publiable telle quelle.
            if en_cours["confirmation"]:
                reg.marquer_publication_confirmation(int(en_cours["confirmation"]),
                                                     "caduque")
            return {"etat": "incoherente", "recuperable": False,
                    "champion_abandonne": nouveau,
                    "champion_actif": reg.champion_courant()["champion_courant"],
                    "remises_en_etat": faits, "reste_a_nettoyer": reste,
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
        # Le pointeur qui fait foi est le pointeur COMMITE. Lire le fichier de travail faisait
        # sauter ce bloc apres une coupure entre son ecriture et son commit : la publication
        # etait annoncee terminee alors que `HEAD` designait encore l'ancien champion, et
        # `current.json` restait modifie dans l'index.
        if (pointeur_commite(depot) or {}).get("champion_courant") != nouveau:
            _ecrire_pointeur(champions, nouveau,
                             {"commit": manifeste["code"]["commit_candidat"],
                              "bundle_sha256": manifeste["code"]["bundle_sha256"]}, tag)
            _git("add", "-A", "training/champions", depot=depot)
            # Les erreurs des operations REQUISES ne sont plus ignorees : une publication
            # annoncee terminee doit l'etre.
            _git("commit", "-q", "-m", "scoring: pointer le champion %s (reprise)\n" % nouveau,
                 depot=depot)
            faits.append("pointeur ecrit sur %s" % nouveau)

        # Verifications FINALES avant cloture : tag, manifeste et pointeur, tous commites et
        # coherents. Une seconde reprise n'aura alors plus rien a faire.
        manques = _controler_publication(depot, champions, nouveau, tag,
                                         manifeste["code"]["bundle_sha256"], etrangers)
        if manques:
            return {"etat": "incomplete", "champion": nouveau, "controles_en_echec": manques,
                    "remises_en_etat": faits,
                    "detail": ("La reprise n'a pas abouti a un etat coherent ; la publication "
                               "reste ouverte et rien n'est annonce comme promu.")}
        reg.cloturer_publication(en_cours["id"],
                                 {"id": nouveau, "commit_code": manifeste["code"]["commit_candidat"],
                                  "bundle_sha256": manifeste["code"]["bundle_sha256"], "tag": tag,
                                  "precedent": en_cours["champion_attendu"],
                                  "manifeste": str(chemin_manifeste)})
        if en_cours["confirmation"]:
            reg.marquer_publication_confirmation(int(en_cours["confirmation"]),
                                                 "publiee", nouveau)
        return {"etat": "terminee_par_reprise", "champion": nouveau, "tag": tag,
                "commit_champion": commit, "remises_en_etat": faits}
    finally:
        _git("checkout", "-q", depart, depot=depot, verifier=False)

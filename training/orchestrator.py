"""Orchestration : blocs -> combats -> cache -> differences appariees.

Un bloc demande quatre combats. Deux d'entre eux — champion contre adversaire, dans les deux
orientations — ne dependent PAS du candidat : leur cle de cache ne contient pas son bundle. Ils
sont donc joues une fois puis resservis a tous les candidats evalues sur ce bloc. Apres
remplissage, un candidat de plus ne coute que deux combats par bloc.

Le parallelisme est un decoupage de la FILE, jamais du moteur : chaque worker est une JVM qui
joue ses combats en serie. Le moteur porte des etats statiques et rien ne prouve leur
isolation entre threads. Deux workers doivent donner exactement les memes resultats qu'un
seul ; c'est un test de reception, pas une esperance.

**La file est constituee a l'echelle de l'ETAPE, pas du bloc.** Une version qui appelait
`jouer` bloc par bloc n'avait jamais que quatre combats a distribuer, donc un seul lot actif :
le parallelisme existait dans le pilote de debit et nulle part dans le parcours evalue.

**Chaque combat termine est enregistre des son arrivee.** Le runner rend une ligne par combat ;
elle est ecrite dans le registre immediatement. Une coupure ne perd donc que le combat en
cours. Les blocs a moitie joues restent partiels : leur difference n'est calculee qu'une fois
les quatre combats presents, et leur absence est REMONTEE au decideur au lieu d'etre ignoree.
"""
from __future__ import annotations

import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import evaluator as mod_eval
import scenarios as mod_sc
import statistics_lab as st

ORIENTATIONS = ("co", "oc", "ho", "oh")
DEPEND_DU_CANDIDAT = {"co": True, "oc": True, "ho": False, "oh": False}


@dataclass
class Combat:
    cle: str
    format: str
    bloc_id: str
    orientation: str
    chemin: Path
    scenario: dict[str, Any]
    ia_gauche: str
    ia_droite: str
    bloc: mod_sc.Bloc | None = None
    options: dict[str, Any] = field(default_factory=dict)


def _ias(orientation: str, ia_c: str, ia_h: str, ia_o: str) -> tuple[str, str]:
    if orientation == "co":
        return ia_c, ia_o
    if orientation == "oc":
        return ia_o, ia_c
    if orientation == "ho":
        return ia_h, ia_o
    return ia_o, ia_h


def preparer(bloc: mod_sc.Bloc, builds: dict[int, dict], moteur: mod_eval.Moteur,
             travail: Path, ia_c: str, ia_h: str, ia_o: str,
             sha_c: str, sha_h: str, sha_o: str) -> list[Combat]:
    """Les quatre combats d'un bloc, avec leur cle de cache.

    Le fichier de scenario est nomme par la CLE DU MATCH : deux candidats evalues en meme
    temps, ou deux etapes qui partagent un bloc, n'ecrivent jamais dans le meme fichier.
    """
    combats = []
    for o in ORIENTATIONS:
        g, d = _ias(o, ia_c, ia_h, ia_o)
        sc = mod_sc.scenario(bloc, builds, g, d)
        # La cle porte les bundles de TOUTES les politiques du combat. Un combat champion
        # contre adversaire n'a donc PAS le bundle du candidat dedans : c'est ce qui le rend
        # reutilisable pour tous les candidats evalues sur ce bloc.
        sha_g, sha_d = _ias(o, sha_c, sha_h, sha_o)
        shas = {g: sha_g, d: sha_d}
        cle = mod_eval.cle_de_match(sc, shas, moteur)
        chemin = Path(travail) / ("%s_%s_%d_%s_%s.json"
                                  % (bloc.format, bloc.adversaire, bloc.indice, o, cle[:12]))
        combats.append(Combat(cle, bloc.format, bloc.cle(), o, chemin, sc, g, d, bloc))
    return combats


def jouer(reg, moteur: mod_eval.Moteur, build: Path, combats: list[Combat],
          workers: int = 1, taille_lot: int = 8, timeout: float = 1800.0,
          echeance: float | None = None,
          progres: Callable[[int, int], None] | None = None) -> dict[str, dict]:
    """Joue ce qui manque, rend {cle -> resultat}. Le cache est consulte AVANT tout travail.

    Chaque combat termine est ecrit dans le registre immediatement, depuis le worker qui l'a
    joue. `echeance` est une date `time.monotonic()` : les lots restants ne sont pas lances au
    dela, et un lot en cours est coupe en conservant ce qu'il a deja rendu.
    """
    resultats: dict[str, dict] = {}
    a_jouer: list[Combat] = []
    vus: set[str] = set()
    for c in combats:
        if c.cle in vus:
            continue
        vus.add(c.cle)
        connu = reg.match_connu(c.cle)
        if connu is not None and connu["erreur"] not in mod_eval.ERREURS_A_REJOUER:
            resultats[c.cle] = {"score_gauche": connu["score_gauche"], "erreur": connu["erreur"],
                                "detail": connu["detail"], "cache": True,
                                "brut": json.loads(connu["brut_json"] or "{}")}
        else:
            a_jouer.append(c)

    if not a_jouer:
        if progres:
            progres(0, 0)
        return resultats

    for c in a_jouer:
        mod_sc.ecrire(c.chemin, c.scenario)

    verrou = threading.Lock()
    compteur = {"faits": 0}
    total = len(a_jouer)

    def _enregistrer(lot: list[Combat], index: int, _chemin: Path, brut: dict) -> None:
        c = lot[index]
        score, err, det = mod_eval.analyser(brut)
        with verrou:
            reg.enregistrer_match(c.cle, c.format, c.bloc_id, c.orientation,
                                  c.ia_gauche, c.ia_droite, score, err, det,
                                  brut.get("duration"),
                                  (brut.get("execution_time_ns") or 0) / 1e6, brut)
            resultats[c.cle] = {"score_gauche": score, "erreur": err, "detail": det,
                                "cache": False, "brut": brut}
            compteur["faits"] += 1
            fait = compteur["faits"]
        if progres:
            progres(fait, total)

    # Une bande par worker, decoupee en lots : chaque lot est une JVM qui amortit la
    # compilation des IA sur ses combats.
    bandes = [a_jouer[i::workers] for i in range(max(1, workers))]
    bandes = [b for b in bandes if b]

    def _une_bande(bande: list[Combat]) -> None:
        for debut in range(0, len(bande), taille_lot):
            lot = bande[debut:debut + taille_lot]
            mod_eval.executer_flux(
                moteur, build, [c.chemin for c in lot], timeout=timeout, echeance=echeance,
                sur_resultat=lambda i, ch, brut, _lot=lot: _enregistrer(_lot, i, ch, brut))

    if len(bandes) <= 1:
        for b in bandes:
            _une_bande(b)
    else:
        with ThreadPoolExecutor(max_workers=len(bandes)) as pool:
            list(pool.map(_une_bande, bandes))

    # Les combats qu'aucune sortie n'a couverts restent ABSENTS du dictionnaire : leur bloc
    # sera declare incomplet, et non complete par un resultat invente.
    return resultats


def difference(bloc: mod_sc.Bloc, combats: list[Combat],
               resultats: dict[str, dict]) -> float | None:
    """d = x(C) - x(H). None si le bloc est INCOMPLET.

    Un bloc a moitie joue ne compte pas : garder la moitie disponible et attendre l'autre est
    la seule facon de ne pas fabriquer une observation biaisee par l'orientation survivante.
    """
    par_o = {c.orientation: resultats.get(c.cle) for c in combats}
    if any(par_o.get(o) is None or par_o[o].get("score_gauche") is None for o in ORIENTATIONS):
        return None
    x_c = (par_o["co"]["score_gauche"] + (1.0 - par_o["oc"]["score_gauche"])) / 2.0
    x_h = (par_o["ho"]["score_gauche"] + (1.0 - par_o["oh"]["score_gauche"])) / 2.0
    return x_c - x_h


def _manquants(combats: list[Combat], resultats: dict[str, dict]) -> list[dict[str, Any]]:
    absents = []
    for c in combats:
        r = resultats.get(c.cle)
        if r is None:
            absents.append({"orientation": c.orientation, "raison": "aucun resultat"})
        elif r.get("score_gauche") is None:
            absents.append({"orientation": c.orientation,
                            "raison": r.get("erreur") or "score absent"})
    return absents


def evaluer_etape(reg, moteur, build, builds, panel: list[str], adversaires_actifs: list,
                  ia_c: str, sha_c: str, ia_h: str, sha_h: str,
                  formats_blocs: dict[str, int], graine: int, travail: Path,
                  vague: str = mod_sc.VAGUE_DEV, workers: int = 1, taille_lot: int = 8,
                  timeout: float = 1800.0, echeance: float | None = None,
                  progres=None) -> tuple[dict[str, list[st.Composante]], dict[str, Any]]:
    """Joue une etape entiere et rend (composantes par format, COUVERTURE).

    La couverture dit exactement ce qui a ete obtenu face a ce qui etait prevu : blocs
    attendus, blocs complets, blocs incomplets avec la raison, adversaires sans donnees. Le
    decideur en a besoin — sans elle, retirer les adversaires sans resultats revenait a
    reponderer ceux qui restaient et a promouvoir sur la moitie du protocole.
    """
    travail = Path(travail)
    travail.mkdir(parents=True, exist_ok=True)
    noms_actifs = [p.ident for p in adversaires_actifs]
    ia_par_pol = {p.ident: (p.deployer(moteur.racine), p.sha256) for p in adversaires_actifs}

    # 1. Tous les blocs de l'etape, tous formats confondus.
    blocs_par_format: dict[str, list[mod_sc.Bloc]] = {}
    for format_nom, nb in formats_blocs.items():
        if nb and nb > 0:
            blocs_par_format[format_nom] = mod_sc.plan_de_blocs(
                format_nom, panel, nb, graine, builds, vague=vague, adversaires=noms_actifs)

    # 2. Tous les combats, en UNE file : c'est elle que les workers se partagent.
    combats_par_bloc: dict[tuple[str, int], list[Combat]] = {}
    file: list[Combat] = []
    for format_nom, blocs in blocs_par_format.items():
        for b in blocs:
            ia_o, sha_o = ia_par_pol[b.adversaire]
            cs = preparer(b, builds, moteur, travail, ia_c, ia_h, ia_o, sha_c, sha_h, sha_o)
            combats_par_bloc[(format_nom, b.indice)] = cs
            file.extend(cs)

    resultats = jouer(reg, moteur, build, file, workers=workers, taille_lot=taille_lot,
                      timeout=timeout, echeance=echeance, progres=progres)

    # 3. Differences et couverture.
    par_format: dict[str, list[st.Composante]] = {}
    couverture: dict[str, Any] = {}
    for format_nom, blocs in blocs_par_format.items():
        d_par_adv: dict[str, list[float]] = {nom: [] for nom in noms_actifs}
        incomplets = []
        for b in blocs:
            cs = combats_par_bloc[(format_nom, b.indice)]
            d = difference(b, cs, resultats)
            if d is None:
                incomplets.append({"bloc": b.cle(), "indice": b.indice,
                                   "adversaire": b.adversaire,
                                   "manquants": _manquants(cs, resultats)})
            else:
                d_par_adv[b.adversaire].append(d)
        par_format[format_nom] = [st.composante(format_nom, adv, ds)
                                  for adv, ds in d_par_adv.items() if ds]
        couverture[format_nom] = {
            "blocs_attendus": len(blocs),
            "blocs_complets": len(blocs) - len(incomplets),
            "blocs_incomplets": incomplets,
            "adversaires_attendus": list(noms_actifs),
            "adversaires_sans_donnees": sorted(a for a in noms_actifs if not d_par_adv[a]),
            "vague": vague,
        }
        couverture[format_nom]["complet"] = (not incomplets
                                             and not couverture[format_nom]
                                             ["adversaires_sans_donnees"])
    return par_format, couverture


# --------------------------------------------------------------------------------------
# Audit BR : remplacement d'une politique FOCALE dans un lobby fixe
# --------------------------------------------------------------------------------------

def rang_normalise(n: int, rang: float) -> float:
    """u = (N - rang) / (N - 1). Rang moyen en cas d'egalite, gere par l'appelant."""
    if n <= 1:
        return 0.5
    return (n - rang) / (n - 1)


def _lobby_fige(builds: dict[int, dict], noms: list[str], graine: int, lobby: int,
                creneau: int) -> tuple[list[int], int]:
    """Composition et graine d'un lobby, tirees UNE fois pour les deux politiques focales.

    Tirer a l'interieur de la boucle C/H donnait deux graines differentes dans une meme
    comparaison : les deux politiques n'etaient pas evaluees sur le meme combat, et la
    difference mesuree melangeait la politique et le tirage.
    """
    rnd = random.Random("br|%d|%d|%d" % (graine, lobby, creneau))
    eleveurs = sorted({b["farmer"]["id"] for b in builds.values()})
    choisis = rnd.sample(eleveurs, min(len(noms), len(eleveurs)))
    return choisis, rnd.randrange(1, 2_000_000_000)


def auditer_br(reg, moteur, build, builds, politiques, ia_c, sha_c, ia_h, sha_h,
               travail: Path, lobbies: int = 12, creneaux: int = 2,
               graine: int = 1, workers: int = 1, taille_lot: int = 8,
               timeout: float = 1800.0, echeance: float | None = None) -> dict[str, Any]:
    """Remplace UNE politique focale par C puis par H dans un lobby autrement fige.

    Les deux creneaux d'un meme lobby forment un groupe correle, pas deux lobbies. Et ce test
    ne repond pas a la meme question que « cinq C contre cinq H » : celui-la mesure un melange
    de populations, celui-ci un remplacement focal. Ne pas melanger leurs scores.

    Aucun VETO en V1 : c'est un rapport periodique. L'appeler audit ne le rend pas independant.
    """
    travail = Path(travail)
    travail.mkdir(parents=True, exist_ok=True)
    ia_par_pol = {p.ident: (p.deployer(moteur.racine), p.sha256) for p in politiques}
    noms = [p.ident for p in politiques]
    n = len(noms)
    if n < 2:
        return {"note": "ligue trop petite pour un lobby BR", "lobbies": 0}

    par_eleveur: dict[int, list[int]] = {}
    for bid, b in builds.items():
        par_eleveur.setdefault(b["farmer"]["id"], []).append(bid)

    file: list[Combat] = []
    plan: list[dict[str, Any]] = []
    for i in range(lobbies):
        for c in range(creneaux):
            choisis, graine_combat = _lobby_fige(builds, noms, graine, i, c)
            focal = (i + c) % n
            entree = {"lobby": i, "creneau": c, "focal": focal, "graine": graine_combat,
                      "cles": {}}
            for etiquette, ia_focal, sha_focal in (("C", ia_c, sha_c), ("H", ia_h, sha_h)):
                groupes, fermiers, equipes = [], [], []
                for pos, e in enumerate(choisis, 1):
                    bid = sorted(par_eleveur[e])[0]
                    ia = ia_focal if (pos - 1) == focal else ia_par_pol[noms[pos - 1]][0]
                    groupes.append([mod_sc._entite(builds[bid], pos * 10000 + 1, pos, pos, ia)])
                    fermiers.append({"id": pos, "name": "br-%d" % pos, "country": "fr"})
                    equipes.append({"id": pos, "name": "br-%d" % pos})
                sc = {"farmers": fermiers, "teams": equipes, "entities": groupes,
                      "fight_type": mod_sc.TYPE_BR, "fight_context": mod_sc.CONTEXTE_BR,
                      "random_seed": graine_combat, "max_turns": mod_sc.TOURS_MAX}
                shas = {ia_focal: sha_focal}
                for pos, nom in enumerate(noms):
                    if pos != focal:
                        shas[ia_par_pol[nom][0]] = ia_par_pol[nom][1]
                cle = mod_eval.cle_de_match(sc, shas, moteur, {"br_focal": focal})
                chemin = travail / ("br_%d_%d_%s_%s.json" % (i, c, etiquette, cle[:12]))
                file.append(Combat(cle, "br", "br-%d-%d" % (i, c), etiquette, chemin, sc,
                                   ia_focal, "lobby"))
                entree["cles"][etiquette] = cle
            plan.append(entree)

    resultats = jouer(reg, moteur, build, file, workers=workers, taille_lot=taille_lot,
                      timeout=timeout, echeance=echeance)

    lignes, incompletes = [], []
    for entree in plan:
        issues = {}
        for etiquette, cle in entree["cles"].items():
            r = resultats.get(cle)
            if r is None or r.get("erreur") in mod_eval.ERREURS_DISQUALIFIANTES:
                issues[etiquette] = None
            else:
                v = r["brut"].get("winner")
                issues[etiquette] = 1.0 if (v is not None and v == entree["focal"]) else 0.0
        if any(v is None for v in issues.values()):
            # Une paire incomplete reste incomplete. Un timeout ou une panne n'est pas une
            # defaite de la politique focale : la compter ainsi fabriquerait un ecart.
            incompletes.append({"lobby": entree["lobby"], "creneau": entree["creneau"],
                                "manquants": sorted(k for k, v in issues.items() if v is None)})
            continue
        for etiquette, gagne in issues.items():
            lignes.append({"lobby": entree["lobby"], "creneau": entree["creneau"],
                           "focal": entree["focal"], "graine": entree["graine"],
                           "politique": etiquette, "gagne": gagne})

    par = {"C": [l for l in lignes if l["politique"] == "C"],
           "H": [l for l in lignes if l["politique"] == "H"]}
    return {
        "lobbies": lobbies, "creneaux_par_lobby": creneaux,
        "paires_prevues": len(plan), "paires_completes": len(plan) - len(incompletes),
        "paires_incompletes": incompletes,
        "combats": len(file),
        "frequence_de_victoire": {k: (sum(l["gagne"] for l in v) / len(v) if v else None)
                                  for k, v in par.items()},
        "_note_appariement": ("Les deux politiques focales jouent le MEME lobby et la MEME "
                              "graine ; seule la politique du creneau focal change."),
        "_note_rang": ("Le rang normalise u = (N - rang)/(N - 1) demande le classement officiel "
                       "du moteur. La sortie du runner ne porte aujourd'hui que le vainqueur : "
                       "seule la frequence de victoire est rapportee, et le rang reste a "
                       "implementer plutot qu'a reconstruire sans validation."),
        "_note_veto": "Aucun veto : rapport periodique, pas condition de promotion.",
        "lignes": lignes,
    }

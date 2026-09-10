"""Orchestration : blocs -> combats -> cache -> differences appariees.

Un bloc demande quatre combats. Deux d'entre eux — champion contre adversaire, dans les deux
orientations — ne dependent PAS du candidat : leur cle de cache ne contient pas son bundle. Ils
sont donc joues une fois puis resservis a tous les candidats evalues sur ce bloc. Apres
remplissage, un candidat de plus ne coute que deux combats par bloc.

Le parallelisme est un decoupage de la FILE, jamais du moteur : chaque worker est une JVM qui
joue ses combats en serie. Le moteur porte des etats statiques et rien ne prouve leur
isolation entre threads. Deux workers doivent donner exactement les memes resultats qu'un
seul ; c'est un test de reception, pas une esperance.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import bundle as mod_bundle
import evaluator as mod_eval
import scenarios as mod_sc
import statistics_lab as st

ORIENTATIONS = ("co", "oc", "ho", "oh")
_DEPEND_DU_CANDIDAT = {"co": True, "oc": True, "ho": False, "oh": False}


@dataclass
class Combat:
    cle: str
    bloc: mod_sc.Bloc
    orientation: str
    chemin: Path
    scenario: dict[str, Any]
    ia_gauche: str
    ia_droite: str


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
    """Les quatre combats d'un bloc, avec leur cle de cache."""
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
        chemin = travail / ("%s_%s_%d_%s.json" % (bloc.format, bloc.adversaire, bloc.indice, o))
        combats.append(Combat(cle, bloc, o, chemin, sc, g, d))
    return combats


def jouer(reg, moteur: mod_eval.Moteur, build: Path, combats: list[Combat],
          workers: int = 1, taille_lot: int = 8, timeout: float = 1800.0,
          progres: Callable[[int, int], None] | None = None) -> dict[str, dict]:
    """Joue ce qui manque, rend {cle -> resultat}. Le cache est consulte AVANT tout travail."""
    resultats: dict[str, dict] = {}
    a_jouer: list[Combat] = []
    for c in combats:
        connu = reg.match_connu(c.cle)
        if connu is not None and connu["erreur"] not in (mod_eval.ERREUR_INFRA,
                                                         mod_eval.ERREUR_MANQUANT):
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

    lots: list[list[Combat]] = [a_jouer[i:i + taille_lot]
                               for i in range(0, len(a_jouer), taille_lot)]
    faits = 0

    def _un_lot(lot: list[Combat]) -> tuple[list[Combat], list[dict]]:
        return lot, mod_eval.executer_lot(moteur, build, [c.chemin for c in lot], timeout)

    if workers <= 1:
        paires = [_un_lot(l) for l in lots]
    else:
        # Un worker = une JVM. Le parallelisme decoupe la file, pas le moteur.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            paires = list(pool.map(_un_lot, lots))

    for lot, bruts in paires:
        for c, brut in zip(lot, bruts):
            score, err, det = mod_eval.analyser(brut)
            reg.enregistrer_match(c.cle, c.bloc.format, c.bloc.cle(), c.orientation,
                                  c.ia_gauche, c.ia_droite, score, err, det,
                                  brut.get("duration"),
                                  (brut.get("execution_time_ns") or 0) / 1e6, brut)
            resultats[c.cle] = {"score_gauche": score, "erreur": err, "detail": det,
                                "cache": False, "brut": brut}
            faits += 1
            if progres:
                progres(faits, len(a_jouer))
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


def evaluer_etape(reg, moteur, build, builds, politiques, ia_c, sha_c, ia_h, sha_h,
                  formats_blocs: dict[str, int], adversaires: list, graine: int,
                  travail: Path, decalage: int = 0, workers: int = 1,
                  progres=None) -> dict[str, list[st.Composante]]:
    """Joue une etape entiere et rend les composantes par format, pretes pour la decision."""
    travail = Path(travail)
    travail.mkdir(parents=True, exist_ok=True)
    par_format: dict[str, list[st.Composante]] = {}
    ia_par_pol = {p.ident: (p.deployer(moteur.racine), p.sha256) for p in adversaires}

    for format_nom, nb in formats_blocs.items():
        if nb <= 0:
            continue
        blocs = mod_sc.plan_de_blocs(format_nom, [p.ident for p in adversaires], nb,
                                     graine, builds, decalage=decalage)
        d_par_adv: dict[str, list[float]] = {p.ident: [] for p in adversaires}
        for b in blocs:
            ia_o, sha_o = ia_par_pol[b.adversaire]
            combats = preparer(b, builds, moteur, travail, ia_c, ia_h, ia_o,
                               sha_c, sha_h, sha_o)
            res = jouer(reg, moteur, build, combats, workers=workers, progres=progres)
            d = difference(b, combats, res)
            if d is not None:
                d_par_adv[b.adversaire].append(d)
        par_format[format_nom] = [st.composante(format_nom, adv, ds)
                                  for adv, ds in d_par_adv.items() if ds]
    return par_format


# --------------------------------------------------------------------------------------
# Audit BR : remplacement d'une politique FOCALE dans un lobby fixe
# --------------------------------------------------------------------------------------

def rang_normalise(n: int, rang: float) -> float:
    """u = (N - rang) / (N - 1). Rang moyen en cas d'egalite, gere par l'appelant."""
    if n <= 1:
        return 0.5
    return (n - rang) / (n - 1)


def auditer_br(reg, moteur, build, builds, politiques, ia_c, sha_c, ia_h, sha_h,
               travail: Path, lobbies: int = 12, creneaux: int = 2,
               graine: int = 1, workers: int = 1) -> dict[str, Any]:
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

    import random
    lignes = []
    for i in range(lobbies):
        rnd = random.Random("br|%d|%d" % (graine, i))
        eleveurs = sorted({b["farmer"]["id"] for b in builds.values()})
        choisis = rnd.sample(eleveurs, min(n, len(eleveurs)))
        par_eleveur: dict[int, list[int]] = {}
        for bid, b in builds.items():
            par_eleveur.setdefault(b["farmer"]["id"], []).append(bid)
        for c in range(creneaux):
            focal = (i + c) % n
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
                      "random_seed": rnd.randrange(1, 2_000_000_000),
                      "max_turns": mod_sc.TOURS_MAX}
                chemin = travail / ("br_%d_%d_%s.json" % (i, c, etiquette))
                mod_sc.ecrire(chemin, sc)
                shas = {ia_focal: sha_focal}
                cle = mod_eval.cle_de_match(sc, shas, moteur, {"br_focal": focal})
                connu = reg.match_connu(cle)
                if connu is not None and connu["erreur"] not in (mod_eval.ERREUR_INFRA,
                                                                 mod_eval.ERREUR_MANQUANT):
                    brut = json.loads(connu["brut_json"] or "{}")
                else:
                    brut = mod_eval.executer_lot(moteur, build, [chemin])[0]
                    _s, err, det = mod_eval.analyser(brut)
                    reg.enregistrer_match(cle, "br", "br-%d-%d" % (i, c), etiquette,
                                          ia_focal, "lobby", None, err, det,
                                          brut.get("duration"),
                                          (brut.get("execution_time_ns") or 0) / 1e6, brut)
                v = brut.get("winner")
                lignes.append({"lobby": i, "creneau": c, "focal": focal,
                               "politique": etiquette,
                               "vainqueur": v,
                               "gagne": 1.0 if (v is not None and v == focal) else 0.0})
    par = {"C": [l for l in lignes if l["politique"] == "C"],
           "H": [l for l in lignes if l["politique"] == "H"]}
    return {
        "lobbies": lobbies, "creneaux_par_lobby": creneaux, "combats": len(lignes),
        "frequence_de_victoire": {k: (sum(l["gagne"] for l in v) / len(v) if v else None)
                                  for k, v in par.items()},
        "_note_rang": ("Le rang normalise u = (N - rang)/(N - 1) demande le classement officiel "
                       "du moteur. La sortie du runner ne porte aujourd'hui que le vainqueur : "
                       "seule la frequence de victoire est rapportee, et le rang reste a "
                       "implementer plutot qu'a reconstruire sans validation."),
        "_note_veto": "Aucun veto : rapport periodique, pas condition de promotion.",
        "lignes": lignes,
    }

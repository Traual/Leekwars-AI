"""Execution des combats, cache de matchs et classification des erreurs.

Le cache est le cœur du debit. Sa cle porte **toutes** les entrees qui influencent le
resultat : les bundles complets de TOUTES les politiques du combat, le scenario resolu, le
mode et le contexte moteur, les cœurs, la graine, l'empreinte du moteur et de ses donnees, le
runner, et la version du parseur de resultats.

L'ancien harnais indexait sur le mode, le nombre de paires, les tours, la graine de selection
et le fichier de vecteur. Cette cle-la ne voyait pas une modification de `Scoring.leek` : elle
pouvait resservir un resultat calcule avec un autre code. C'est exactement le controle que le
cahier des charges demande de remplacer.

La version du parseur en fait partie : si l'interpretation d'une sortie change, les resumes
doivent etre invalides meme quand le combat, lui, n'a pas bouge.

**Un lot rend ses resultats AU FIL DE L'EAU.** Le runner ecrit une ligne par combat termine et
la vide immediatement ; `executer_flux` la lit et la remonte tout de suite. Une coupure — par
echeance de budget ou par panne — ne perd donc que le combat en cours, jamais les combats deja
joues du meme lot. L'ancienne version attendait la fin du processus : une interruption au
deuxieme lot rendait les huit combats du premier introuvables alors qu'ils etaient joues.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

RACINE = Path(__file__).resolve().parent
SOURCE_RUNNER = RACINE / "tools" / "BatchRunner.java"
PREFIXE = "__TRAUAL_RESULT__\t"

# A incrementer des que l'interpretation d'une sortie de combat change.
# 2 : lecture des erreurs systeme du moteur (chargement d'IA) et du champ `exception`.
# 3 : moteur 3.00 — reveils de plante (PLANT_AWAKE 17 / PLANT_ASLEEP 18) comptes par plante ;
#     les actions entre ces deux bornes sont celles de la plante, pas de l'entite dont c'est
#     le tour.
VERSION_PARSEUR = 3


# --------------------------------------------------------------------------------------
# Environnement
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Moteur:
    """Le generateur et son environnement, resolus en chemins ABSOLUS.

    `NativeFileSystem` du compilateur LeekScript resout les includes depuis sa propre racine :
    un chemin relatif y devient un chemin faux sans erreur visible. La normalisation par
    `resolve()` vient de la branche hybride, ou elle corrigeait deja ce defaut.
    """
    racine: Path
    jar: Path
    leekscript_jar: Path | None
    outils: Path            # repertoire contenant java et javac, PAS forcement <home>/bin
    empreinte: str

    @staticmethod
    def detecter(racine: str | Path, java_home: str | Path | None = None) -> "Moteur":
        racine = Path(racine).resolve()
        jar = (racine / "generator.jar").resolve()
        if not jar.exists():
            raise FileNotFoundError("generator.jar introuvable sous %s" % racine)
        ls = (racine / "leekscript" / "leekscript.jar").resolve()
        # Le repertoire des OUTILS, pas le JAVA_HOME : sous Windows, le java du PATH est
        # souvent un shim Oracle (Common Files/Oracle/Java/javapath) qui contient java.exe et
        # javac.exe cote a cote, sans repertoire bin. Deduire <home>/bin d'un tel chemin donne
        # un repertoire inexistant, et l'erreur ne dit pas pourquoi.
        if java_home:
            base = Path(java_home).resolve()
            outils = base / "bin" if (base / "bin").is_dir() else base
        else:
            java = shutil.which("java")
            if not java:
                raise FileNotFoundError("aucun java sur le PATH")
            outils = Path(java).resolve().parent
        if not any((outils / ("javac" + s)).exists() for s in (".exe", "")):
            raise FileNotFoundError(
                "javac introuvable dans %s : il faut un JDK, pas seulement un JRE." % outils)
        h = hashlib.sha256()
        for p in (jar, ls if ls.exists() else None):
            if p is None:
                continue
            h.update(p.name.encode() + b"\0")
            h.update(hashlib.sha256(p.read_bytes()).digest())
        donnees = racine / "data"
        if donnees.is_dir():
            for f in sorted(donnees.rglob("*.json")):
                h.update(str(f.relative_to(donnees)).replace("\\", "/").encode() + b"\0")
                h.update(hashlib.sha256(f.read_bytes()).digest())
        return Moteur(racine, jar, ls if ls.exists() else None, outils, h.hexdigest())

    def binaire(self, nom: str) -> str:
        """Chemin d'un outil du JDK. Windows veut l'extension, Unix non."""
        suffixe = ".exe" if sys.platform == "win32" else ""
        return str(self.outils / (nom + suffixe))

    def classpath(self, build_runner: Path) -> str:
        parties = [str(build_runner), str(self.jar)]
        if self.leekscript_jar:
            parties.append(str(self.leekscript_jar))
        return os.pathsep.join(parties)

    def version_java(self) -> str:
        r = subprocess.run([self.binaire("java"), "-version"],
                           capture_output=True, text=True)
        return (r.stderr or r.stdout).strip().split("\n")[0]


def empreinte_runner() -> str:
    return hashlib.sha256(SOURCE_RUNNER.read_bytes()).hexdigest()


def compiler_runner(moteur: Moteur, build: Path) -> Path:
    """Compile BatchRunner une fois, et seulement si sa source ou le JAR ont bouge."""
    build = Path(build)
    build.mkdir(parents=True, exist_ok=True)
    classe = build / "training" / "tools" / "BatchRunner.class"
    plus_recent = max(SOURCE_RUNNER.stat().st_mtime, moteur.jar.stat().st_mtime)
    if classe.exists() and classe.stat().st_mtime >= plus_recent:
        return build
    cp = os.pathsep.join([str(moteur.jar)]
                         + ([str(moteur.leekscript_jar)] if moteur.leekscript_jar else []))
    subprocess.run([moteur.binaire("javac"), "-cp", cp,
                    "-d", str(build), str(SOURCE_RUNNER)], check=True,
                   capture_output=True)
    return build


# --------------------------------------------------------------------------------------
# Identite d'un match
# --------------------------------------------------------------------------------------

def cle_de_match(scenario: dict[str, Any], bundles: dict[str, str], moteur: Moteur,
                 options: dict[str, Any] | None = None) -> str:
    """Serialisation canonique de tout ce qui influence le resultat.

    `bundles` associe chaque chemin d'IA present dans le scenario a l'empreinte du bundle
    servi. Deux politiques differentes sur le meme chemin donnent donc deux cles differentes,
    ce qu'un simple nom de repertoire ne garantirait pas.
    """
    charge = {
        "scenario": scenario,
        "bundles": dict(sorted(bundles.items())),
        "moteur": moteur.empreinte,
        "runner": empreinte_runner(),
        "parseur": VERSION_PARSEUR,
        "options": options or {},
    }
    brut = json.dumps(charge, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------
# Classification des erreurs
# --------------------------------------------------------------------------------------

ERREUR_AUCUNE = "aucune"
# Le moteur emet une seule action pour plusieurs causes : depassement du plafond
# d'operations, StackOverflow, exception arithmetique, modification pendant iteration, IA
# invalide (EntityAI.java:385-400). L'action ne porte que [1002, id] ; la CAUSE vit dans les
# logs de l'entite. L'etiquette reste donc volontairement large : elle ne pretend pas
# distinguer un avortement au plafond d'une vraie exception. Les deux CONSERVENT le resultat.
ERREUR_IA = "tour_avorte_ou_exception"  # comportement de l'IA : resultat CONSERVE
ERREUR_COMPILATION = "compilation"      # candidat invalide
ERREUR_INFRA = "infrastructure"         # worker, stockage, environnement : a rejouer
ERREUR_MANQUANT = "resultat_manquant"   # timeout du banc : ni victoire ni nul
ERREUR_CHARGEMENT = "ia_non_chargee"    # le moteur n'a jamais eu d'IA a executer

# Erreurs SYSTEME du moteur qui signifient « cette IA n'a jamais tourne » (ordinaux de
# `leekscript.common.Error`) : fichier introuvable, aucune IA equipee, IA invalide, echec de
# compilation Java, code trop gros. Elles se distinguent des erreurs de COMPORTEMENT —
# plafond d'operations (101), interruption (64), debordement de pile (76) — qui sont du jeu
# reel et dont le resultat compte.
CLES_CHARGEMENT = {
    16: "AI_NOT_EXISTING",
    60: "NO_AI_EQUIPPED",
    61: "INVALID_AI",
    62: "COMPILE_JAVA",
    66: "CODE_TOO_LARGE",
}
NIVEAU_SERREUR = 8


def erreurs_de_chargement(brut: dict[str, Any]) -> list[dict[str, Any]]:
    """Les erreurs systeme du moteur qui disent qu'une IA n'a pas ete chargee.

    C'est l'information du MOTEUR, pas une heuristique sur le cout : un combat lent, perdu, ou
    dont l'IA a explose son plafond d'operations reste un combat valide. Seul un combat ou le
    moteur n'a jamais eu d'IA a executer sort des mesures.
    """
    trouvees = []
    for entree in brut.get("system_errors") or []:
        try:
            entite, niveau, cle = entree[0], entree[1], entree[2]
        except (IndexError, TypeError):
            continue
        if niveau == NIVEAU_SERREUR and cle in CLES_CHARGEMENT:
            trouvees.append({"entite": entite, "cle": cle, "nom": CLES_CHARGEMENT[cle]})
    return trouvees


def classer(brut: dict[str, Any]) -> tuple[str, str]:
    """(categorie, detail). Distingue le COMPORTEMENT de l'IA de la PANNE du banc.

    L'ancien harnais rendait invalide tout combat portant `ai_errors`, ce qui retirait de
    l'echantillon les mauvaises performances — precisement celles qu'on veut mesurer. Ici une
    exception de l'IA laisse le resultat dans l'echantillon : c'est du jeu, pas une panne.
    De meme, un depassement du plafond d'operations est le comportement reel du moteur.

    En revanche un combat dont les IA n'ont pas ete CHARGEES n'est pas du jeu : il se termine
    vite parce qu'il n'a rien calcule. Le moteur le dit lui-meme par une erreur systeme.
    """
    if "runner_error" in brut:
        texte = str(brut["runner_error"])
        if "Invalid AI" in texte or "compil" in texte.lower():
            return ERREUR_COMPILATION, texte
        return ERREUR_INFRA, texte
    if brut.get("exception"):
        # Panne du generateur PENDANT la generation du combat : le combat n'a pas eu lieu,
        # il est a rejouer. Sans cette lecture il devenait un simple « resultat manquant ».
        return ERREUR_INFRA, "exception du generateur : %s" % brut["exception"]
    chargement = erreurs_de_chargement(brut)
    if chargement:
        noms = sorted({e["nom"] for e in chargement})
        return ERREUR_CHARGEMENT, ("%d entite(s) sans IA chargee : %s"
                                   % (len({e["entite"] for e in chargement}), ", ".join(noms)))
    if brut.get("winner") is None:
        return ERREUR_MANQUANT, "aucun vainqueur officiel dans la sortie"
    if brut.get("ai_errors"):
        return ERREUR_IA, "%d tour(s) avorte(s) ou en exception" % len(brut["ai_errors"])
    return ERREUR_AUCUNE, ""


# Categories dont le resultat ne compte PAS : ni score, ni mesure de debit.
ERREURS_DISQUALIFIANTES = (ERREUR_COMPILATION, ERREUR_INFRA, ERREUR_MANQUANT,
                           ERREUR_CHARGEMENT)
# Categories qu'il faut REJOUER : le combat n'a pas eu lieu dans des conditions valables.
ERREURS_A_REJOUER = (ERREUR_INFRA, ERREUR_MANQUANT, ERREUR_CHARGEMENT)


# --------------------------------------------------------------------------------------
# Resultat officiel
# --------------------------------------------------------------------------------------

@dataclass
class Resultat:
    cle: str
    vainqueur: int | None            # index d'equipe officiel du moteur, -1 = nul
    score_gauche: float | None       # 1 victoire, 0.5 nul, 0 defaite, du point de vue gauche
    erreur: str
    detail: str
    duree_tours: int | None
    ms_execution: float | None
    brut: dict[str, Any]

    def score(self, camp: str) -> float | None:
        if self.score_gauche is None:
            return None
        return self.score_gauche if camp == "gauche" else 1.0 - self.score_gauche


def _score_gauche(brut: dict[str, Any]) -> float | None:
    """Issue OFFICIELLE, jamais reconstruite depuis la vie restante.

    Le moteur rend l'index d'equipe gagnante ; -1 signifie nul. La vie restante et les
    operations restent journalisees comme diagnostic, mais aucune promotion n'en depend :
    optimiser une reconstruction interne, c'est optimiser son propre thermometre.
    """
    v = brut.get("winner")
    if v is None:
        return None
    if v < 0:
        return 0.5
    return 1.0 if v == 0 else 0.0


def analyser(brut: dict[str, Any]) -> tuple[float | None, str, str]:
    erreur, detail = classer(brut)
    if erreur in ERREURS_DISQUALIFIANTES:
        return None, erreur, detail
    return _score_gauche(brut), erreur, detail


def valide_pour_le_debit(brut: dict[str, Any]) -> tuple[bool, str]:
    """Ce combat peut-il nourrir une mesure de debit ? (oui/non, raison).

    **C'est la fonction appelee par `calibrate`**, et c'est elle que le test de reception
    exerce. Une version definie dans le test seul ne protegeait rien : le pilote comptait
    valides des combats de quatre dixiemes de seconde ou l'IA levait a chaque tour.

    Le critere n'est pas le cout ni l'issue : un combat lent, perdu, ou dont l'IA epuise son
    plafond d'operations compte pleinement. Seul le diagnostic du moteur disqualifie.
    """
    _score, err, detail = analyser(brut)
    if err in ERREURS_DISQUALIFIANTES:
        return False, "%s : %s" % (err, detail)
    if "system_errors" not in brut and "runner_error" not in brut:
        return False, ("sortie anterieure au diagnostic de chargement : impossible de "
                       "distinguer un combat joue d'un combat sans IA")
    return True, ""


# --------------------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------------------

def tuer_arbre(proc: subprocess.Popen) -> None:
    """Tue le processus ET SA DESCENDANCE.

    Sous Windows, le `java` du PATH est souvent le shim Oracle
    (`Common Files/Oracle/Java/javapath`), qui lance le VRAI JVM dans un processus FILS. Tuer
    le shim ne tue alors que le shim : la JVM continue a tourner, invisible, et mange la
    machine — c'est exactement ce qui avait laisse vingt JVM abandonnees apres une campagne
    interrompue. Une echeance de budget qui laisse des orphelins ne borne rien du tout.
    """
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, check=False)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


def commande_lot(moteur: Moteur, build_runner: Path, scenarios: list[Path]) -> list[str]:
    """La commande d'un worker. Isolee pour qu'un test de reception puisse exercer le FLUX
    reel — lecture au fil de l'eau, echeance, resultats partiels — sans lancer une JVM."""
    return [moteur.binaire("java"), "-cp", moteur.classpath(build_runner),
            "training.tools.BatchRunner", *[str(p) for p in scenarios]]


def executer_flux(moteur: Moteur, build_runner: Path, scenarios: list[Path],
                  timeout: float = 1800.0, echeance: float | None = None,
                  sur_resultat: Callable[[int, Path, dict[str, Any]], None] | None = None
                  ) -> list[dict[str, Any]]:
    """Un worker = UNE JVM qui joue ses scenarios en serie, et rend chacun DES SA FIN.

    Pas de threads dans le moteur : il porte des etats statiques, et rien ne prouve leur
    isolation. La JVM est reutilisee pour amortir la compilation des IA, ce que fait deja
    BatchRunner.

    `echeance` est une date `time.monotonic()` : le lot est coupe quand elle est atteinte, et
    les combats DEJA TERMINES sont conserves. C'est ce qui permet a un budget de temps de
    s'appliquer sans jeter le travail paye.
    """
    if not scenarios:
        return []
    cmd = commande_lot(moteur, build_runner, scenarios)
    limite = time.monotonic() + float(timeout)
    if echeance is not None:
        limite = min(limite, float(echeance))
    if limite <= time.monotonic():
        return [{"runner_error": "echeance atteinte avant le lancement du lot"}
                for _ in scenarios]

    # `start_new_session` place la JVM dans son propre groupe de processus hors Windows, pour
    # que `tuer_arbre` puisse emporter toute sa descendance d'un seul signal.
    proc = subprocess.Popen(cmd, cwd=str(moteur.racine), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1,
                            encoding="utf-8", errors="replace",
                            start_new_session=(sys.platform != "win32"))
    arrives: dict[int, dict[str, Any]] = {}
    coupe = threading.Event()

    def _couper() -> None:
        coupe.set()
        tuer_arbre(proc)

    chien = threading.Timer(max(0.0, limite - time.monotonic()), _couper)
    chien.daemon = True
    chien.start()
    try:
        for ligne in proc.stdout:
            if not ligne.startswith(PREFIXE):
                continue
            corps = ligne[len(PREFIXE):].rstrip("\r\n")
            index, charge = corps.split("\t", 1)
            i = int(index)
            brut = json.loads(charge)
            arrives[i] = brut
            if sur_resultat is not None:
                sur_resultat(i, scenarios[i], brut)
    finally:
        chien.cancel()
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=30)
        except Exception:
            tuer_arbre(proc)

    if coupe.is_set():
        manquant = ("lot coupe a l'echeance ; %d combat(s) du lot termines et conserves"
                    % len(arrives))
    else:
        manquant = "aucune sortie du runner pour ce scenario"
    return [arrives.get(i, {"runner_error": manquant}) for i in range(len(scenarios))]


def executer_lot(moteur: Moteur, build_runner: Path, scenarios: list[Path],
                 timeout: float = 1800.0) -> list[dict[str, Any]]:
    """Forme bloquante d'`executer_flux`, conservee pour les appels qui n'ont rien a
    enregistrer au fil de l'eau."""
    return executer_flux(moteur, build_runner, scenarios, timeout)

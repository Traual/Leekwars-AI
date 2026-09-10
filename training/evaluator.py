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
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parent
SOURCE_RUNNER = RACINE / "tools" / "BatchRunner.java"
PREFIXE = "__TRAUAL_RESULT__\t"

# A incrementer des que l'interpretation d'une sortie de combat change.
VERSION_PARSEUR = 1


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


def compiler_runner(moteur: Moteur, build: Path) -> Path:
    """Compile BatchRunner une fois, et seulement si sa source ou le JAR ont bouge."""
    build = Path(build)
    build.mkdir(parents=True, exist_ok=True)
    classe = build / "training" / "tools" / "BatchRunner.class"
    plus_recent = max(SOURCE_RUNNER.stat().st_mtime, moteur.jar.stat().st_mtime)
    if classe.exists() and classe.stat().st_mtime >= plus_recent:
        return build
    cp = os.pathsep.join([str(moteur.jar)] + ([str(moteur.leekscript_jar)] if moteur.leekscript_jar else []))
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
        "runner": hashlib.sha256(SOURCE_RUNNER.read_bytes()).hexdigest(),
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
# logs de l'entite, que BatchRunner n'extrait pas encore. L'etiquette reste donc volontairement
# large : elle ne pretend pas distinguer un avortement au plafond d'une vraie exception.
# Les deux CONSERVENT le resultat, donc aucune decision ne depend de cette distinction
# aujourd'hui. La separer demandera de faire remonter les logs.
ERREUR_IA = "tour_avorte_ou_exception"  # comportement de l'IA : resultat CONSERVE
ERREUR_COMPILATION = "compilation"      # candidat invalide
ERREUR_INFRA = "infrastructure"         # worker, stockage, environnement : a rejouer
ERREUR_MANQUANT = "resultat_manquant"   # timeout du banc : ni victoire ni nul


def classer(brut: dict[str, Any]) -> tuple[str, str]:
    """(categorie, detail). Distingue le COMPORTEMENT de l'IA de la PANNE du banc.

    L'ancien harnais rendait invalide tout combat portant `ai_errors`, ce qui retirait de
    l'echantillon les mauvaises performances — precisement celles qu'on veut mesurer. Ici une
    exception de l'IA laisse le resultat dans l'echantillon : c'est du jeu, pas une panne.
    De meme, un depassement du plafond d'operations est le comportement reel du moteur.
    """
    if "runner_error" in brut:
        texte = str(brut["runner_error"])
        if "Invalid AI" in texte or "compil" in texte.lower():
            return ERREUR_COMPILATION, texte
        return ERREUR_INFRA, texte
    if brut.get("winner") is None:
        return ERREUR_MANQUANT, "aucun vainqueur officiel dans la sortie"
    if brut.get("ai_errors"):
        return ERREUR_IA, "%d tour(s) avorte(s) ou en exception" % len(brut["ai_errors"])
    return ERREUR_AUCUNE, ""


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
    if erreur in (ERREUR_COMPILATION, ERREUR_INFRA, ERREUR_MANQUANT):
        return None, erreur, detail
    return _score_gauche(brut), erreur, detail


# --------------------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------------------

def executer_lot(moteur: Moteur, build_runner: Path, scenarios: list[Path],
                 timeout: float = 1800.0) -> list[dict[str, Any]]:
    """Un worker = UNE JVM qui joue ses scenarios en serie.

    Pas de threads : le moteur porte des etats statiques, et rien ne prouve leur isolation.
    La JVM est reutilisee pour amortir la compilation des IA, ce que fait deja BatchRunner.
    """
    if not scenarios:
        return []
    cmd = [moteur.binaire("java"), "-cp", moteur.classpath(build_runner),
           "training.tools.BatchRunner", *[str(p) for p in scenarios]]
    debut = time.monotonic()
    try:
        fini = subprocess.run(cmd, cwd=str(moteur.racine), stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Timeout du banc : resultat MANQUANT pour chaque scenario du lot. Jamais un nul.
        return [{"runner_error": "timeout du lot apres %.0f s" % (time.monotonic() - debut)}
                for _ in scenarios]
    resultats: dict[int, dict[str, Any]] = {}
    for ligne in fini.stdout.split("\n"):
        if not ligne.startswith(PREFIXE):
            continue
        corps = ligne[len(PREFIXE):]
        index, charge = corps.split("\t", 1)
        resultats[int(index)] = json.loads(charge)
    return [resultats.get(i, {"runner_error": "aucune sortie du runner pour ce scenario"})
            for i in range(len(scenarios))]

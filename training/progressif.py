"""Evaluation PROGRESSIVE : paliers fixes, bornes hautes unilaterales, arrets defavorables.

Une etape n'est plus jouee d'un bloc. Son plan de blocs reste le meme — flux deterministe par
(campagne, format, vague), prefixes emboites — mais il est decoupe en PALIERS fixes avant tout
resultat : des effectifs cumulatifs par format. Seuls les combats du palier courant sont soumis.
Une decision ne se prend qu'a la fin d'un palier, sur TOUS ses blocs complets ; un palier qui
n'a pas tout rendu (echeance, panne) laisse l'etape INTERROMPUE, reprenable sans doublon grace
au cache de matchs.

A chaque palier, trois familles de tests peuvent ARRETER le candidat, jamais le promouvoir :

- plancher solo ou team : borne HAUTE de l'ecart sous le plancher (-0,05)  -> REJET_STATISTIQUE
- futilite eleveur : borne haute sous le gain minimal (+0,01), ou sous un seuil plus tolerant
  inscrit pour le premier palier eleveur                                   -> ABANDON_CRIBLE
- objectif pondere : borne haute sous son seuil, calculee seulement quand TOUS les formats
  ponderes ont ete mesures — un format absent ne vaut pas zero             -> REJET_STATISTIQUE

**Approximations, nommees.** Les bornes sont celles de Student avec des degres de liberte de
Welch-Satterthwaite, comme au decideur de confirmation. La variance de chaque couple (format,
adversaire) est RETRECIE vers une variance a priori mesuree sur une campagne precedente
(`crible.variance_a_priori`, avec `crible.ddl_a_priori` degres de liberte fictifs) : une variance
empirique nulle sur trois blocs ne prouve aucune absence d'incertitude, et un seul bloc par
adversaire reste bornable. **Multiplicite** : chaque famille de tests recoit un risque alpha,
reparti a parts egales (Bonferroni) sur les paliers ou elle est evaluee ; le risque de rejeter
a tort un candidat reellement au-dessus des seuils est majore par la somme des trois alphas,
sous l'approximation de Student. Aucune garantie non asymptotique n'est revendiquee.

Une etape de crible sur un sous-panel (trois adversaires) est une decision HEURISTIQUE sur ce
sous-panel : le rapport le dit. Aucun arret n'est une preuve qu'une idee est mauvaise.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import statistics_lab as st

ABANDON_CRIBLE = "ABANDON_CRIBLE"
REJET_STATISTIQUE = "REJET_STATISTIQUE"
INTERROMPU = "INTERROMPU"
POURSUIVI = "POURSUIVI"
ARRETS_DEFINITIFS = (ABANDON_CRIBLE, REJET_STATISTIQUE)

FAMILLES = ("plancher", "futilite", "objectif")


# --------------------------------------------------------------------------------------
# Quantile de Student pour un alpha quelconque
# --------------------------------------------------------------------------------------

def _beta_incomplete_reguliere(x: float, a: float, b: float) -> float:
    """I_x(a, b) par fraction continue de Lentz (Numerical Recipes, betacf)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_facteur = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(x) + b * math.log(1.0 - x))
    symetrique = x > (a + 1.0) / (a + b + 2.0)
    if symetrique:
        a, b, x = b, a, 1.0 - x
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = tiny if abs(d) < tiny else d
    d = 1.0 / d
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    valeur = math.exp(ln_facteur) * h / a
    return 1.0 - valeur if symetrique else valeur


def cdf_t(t: float, nu: float) -> float:
    x = nu / (nu + t * t)
    queue = 0.5 * _beta_incomplete_reguliere(x, nu / 2.0, 0.5)
    return 1.0 - queue if t > 0 else queue


def quantile_t(alpha: float, nu: float) -> float:
    """Quantile unilateral 1-alpha de Student a nu degres de liberte (bissection)."""
    if not 0.0 < alpha < 0.5:
        raise ValueError("alpha hors de ]0 ; 0,5[ : %r" % alpha)
    nu = max(nu, 0.5)
    bas, haut = 0.0, 1.0
    while cdf_t(haut, nu) < 1.0 - alpha:
        haut *= 2.0
        if haut > 1e7:
            return haut
    for _ in range(80):
        milieu = (bas + haut) / 2.0
        if cdf_t(milieu, nu) < 1.0 - alpha:
            bas = milieu
        else:
            haut = milieu
    return haut


# --------------------------------------------------------------------------------------
# Paliers
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Palier:
    rang: int
    blocs: dict[str, int]


def paliers_de(spec: dict[str, Any]) -> list[Palier]:
    """Les paliers d'une etape, CUMULATIFS. Sans `paliers`, un seul : l'etape entiere.

    Refuse un plan incoherent : un palier doit rester sous les blocs de l'etape, les effectifs
    ne decroissent jamais d'un palier au suivant, et le dernier palier EST l'etape.
    """
    blocs = {f: int(n) for f, n in (spec.get("blocs") or {}).items() if n}
    brut = spec.get("paliers")
    if not brut:
        return [Palier(0, dict(blocs))]
    out: list[Palier] = []
    precedent: dict[str, int] = {}
    for rang, p in enumerate(brut):
        courant = {f: int(n) for f, n in (p or {}).items() if n}
        for f, n in courant.items():
            if f not in blocs:
                raise ValueError("palier %d : format %s absent des blocs de l'etape" % (rang, f))
            if n > blocs[f]:
                raise ValueError("palier %d : %d blocs %s pour %d prevus" % (rang, n, f, blocs[f]))
        for f, n in precedent.items():
            if courant.get(f, 0) < n:
                raise ValueError("palier %d : les blocs %s decroissent (%d -> %d)"
                                 % (rang, f, n, courant.get(f, 0)))
        out.append(Palier(rang, courant))
        precedent = courant
    if out[-1].blocs != blocs:
        raise ValueError("le dernier palier %s n'est pas l'etape %s" % (out[-1].blocs, blocs))
    return out


def nombre_de_regards(paliers: list[Palier], famille: str, poids: dict[str, float]) -> int:
    """Combien de paliers evaluent cette famille de tests (base de la repartition d'alpha)."""
    n = 0
    for p in paliers:
        if famille == "plancher" and any(f in p.blocs for f in ("solo", "team")):
            n += 1
        elif famille == "futilite" and "farmer" in p.blocs:
            n += 1
        elif famille == "objectif" and all(p.blocs.get(f, 0) > 0
                                           for f, w in poids.items() if w > 0):
            n += 1
    return max(1, n)


# --------------------------------------------------------------------------------------
# Bornes hautes a variance retrecie
# --------------------------------------------------------------------------------------

def _termes(comps: list[st.Composante], sigma2_0: float, nu0: float,
            poids_format: float = 1.0) -> tuple[float, list[float], list[float]]:
    """(estimation ponderee, a_k, ddl_k) d'un format, adversaires equiponderes.

    Variance retrecie d'un couple : (nu0 * s0^2 + (n - 1) * s^2) / (nu0 + n - 1), avec nu0 + n - 1
    degres de liberte. Un couple a un seul bloc garde la variance a priori.
    """
    comps = [c for c in comps if c.n > 0]
    if not comps:
        return 0.0, [], []
    w = poids_format / len(comps)
    est = sum(w * c.moyenne for c in comps)
    a, ddl = [], []
    for c in comps:
        k = c.n - 1 if c.n > 1 and math.isfinite(c.variance) else 0
        s2 = c.variance if k > 0 else 0.0
        post = (nu0 * sigma2_0 + k * s2) / (nu0 + k)
        a.append(w * w * post / c.n)
        ddl.append(nu0 + k)
    return est, a, ddl


def _borne(est: float, a: list[float], ddl: list[float], alpha: float) -> tuple[float | None, float | None]:
    total = sum(a)
    if not a or total <= 0.0:
        return None, None
    denom = sum(x * x / d for x, d in zip(a, ddl) if d > 0)
    if denom <= 0.0:
        return None, None
    nu = total * total / denom
    return est + quantile_t(alpha, nu) * math.sqrt(total), nu


def borne_haute_format(comps: list[st.Composante], sigma2_0: float, nu0: float,
                       alpha: float) -> dict[str, Any]:
    est, a, ddl = _termes(comps, sigma2_0, nu0)
    haute, nu = _borne(est, a, ddl, alpha)
    return {"estimation": est, "borne_haute": haute, "ddl": nu,
            "blocs": sum(c.n for c in comps), "adversaires": len([c for c in comps if c.n])}


def borne_haute_objectif(par_format: dict[str, list[st.Composante]], poids: dict[str, float],
                         a_priori: dict[str, float], nu0: float, alpha: float) -> dict[str, Any]:
    est, a, ddl = 0.0, [], []
    for f, w in poids.items():
        if w <= 0:
            continue
        e, af, df = _termes(par_format.get(f) or [], a_priori[f], nu0, w)
        est += e
        a += af
        ddl += df
    haute, nu = _borne(est, a, ddl, alpha)
    return {"estimation": est, "borne_haute": haute, "ddl": nu}


# --------------------------------------------------------------------------------------
# Jugement d'un palier
# --------------------------------------------------------------------------------------

@dataclass
class Jugement:
    rang: int
    arret: str | None                     # None = on continue
    raisons: list[str] = field(default_factory=list)
    tests: dict[str, Any] = field(default_factory=dict)


def juger_palier(par_format: dict[str, list[st.Composante]], palier: Palier,
                 paliers: list[Palier], cfg: dict[str, Any], confirmation: bool = False
                 ) -> Jugement:
    """Applique les trois familles de tests d'arret a la fin d'un palier complet.

    `cfg` porte `objectif` (poids, planchers, gain minimal) et `crible` (alphas, variances a
    priori, seuil du premier palier eleveur). En confirmation, seules les issues DEFAVORABLES
    arretent ; le dernier palier y est laisse au decideur complet.
    """
    obj = cfg["objectif"]
    crible = cfg.get("crible") or {}
    poids = {f: float(w) for f, w in obj["poids"].items() if float(w) > 0}
    a_priori = {f: float(v) for f, v in (crible.get("variance_a_priori") or {}).items()}
    nu0 = float(crible.get("ddl_a_priori", 4))
    alphas = {"plancher": float(crible.get("alpha_plancher", 0.05)),
              "futilite": float(crible.get("alpha_futilite", 0.05)),
              "objectif": float(crible.get("alpha_objectif", 0.05))}
    if confirmation:
        alphas = {k: float(crible.get("alpha_%s_confirmation" % k, v)) for k, v in alphas.items()}
    regards = {fam: nombre_de_regards(paliers, fam, poids) for fam in FAMILLES}
    j = Jugement(palier.rang, None)

    def _alpha(fam: str) -> float:
        return alphas[fam] / regards[fam]

    # 1. Planchers solo et team.
    for f in ("solo", "team"):
        comps = par_format.get(f)
        if not comps or f not in obj.get("planchers_empiriques", {}):
            continue
        b = borne_haute_format(comps, a_priori.get(f, 0.1), nu0, _alpha("plancher"))
        plancher = float(obj["planchers_empiriques"][f])
        b.update({"seuil": plancher, "alpha": _alpha("plancher")})
        j.tests["plancher_%s" % f] = b
        if b["borne_haute"] is not None and b["borne_haute"] < plancher and j.arret is None:
            j.arret = REJET_STATISTIQUE
            j.raisons.append("%s : borne haute %.4f sous le plancher %.2f (%d blocs, %d adversaires)"
                             % (f, b["borne_haute"], plancher, b["blocs"], b["adversaires"]))

    # 2. Futilite eleveur.
    comps = par_format.get("farmer")
    if comps:
        premier = next((p.rang for p in paliers if "farmer" in p.blocs), None)
        seuil = float(obj["gain_minimal_farmer"])
        if palier.rang == premier and crible.get("seuil_premier_palier_farmer") is not None \
                and not confirmation:
            seuil = float(crible["seuil_premier_palier_farmer"])
        b = borne_haute_format(comps, a_priori.get("farmer", 0.1), nu0, _alpha("futilite"))
        b.update({"seuil": seuil, "alpha": _alpha("futilite")})
        j.tests["futilite_farmer"] = b
        if b["borne_haute"] is not None and b["borne_haute"] < seuil and j.arret is None:
            j.arret = ABANDON_CRIBLE
            j.raisons.append("eleveur : borne haute %.4f sous le seuil %.3f (%d blocs, %d adversaires)"
                             % (b["borne_haute"], seuil, b["blocs"], b["adversaires"]))

    # 3. Objectif pondere, seulement quand tous les formats ponderes sont mesures.
    if all(par_format.get(f) for f in poids):
        b = borne_haute_objectif(par_format, poids, {f: a_priori.get(f, 0.1) for f in poids},
                                 nu0, _alpha("objectif"))
        seuil = float(crible.get("seuil_objectif", 0.0))
        b.update({"seuil": seuil, "alpha": _alpha("objectif")})
        j.tests["objectif"] = b
        if b["borne_haute"] is not None and b["borne_haute"] < seuil and j.arret is None:
            j.arret = REJET_STATISTIQUE
            j.raisons.append("objectif pondere : borne haute %.4f sous %.3f"
                             % (b["borne_haute"], seuil))
    else:
        j.tests["objectif"] = {"differe": "formats ponderes non tous mesures : un format absent "
                                          "ne vaut pas zero"}
    return j

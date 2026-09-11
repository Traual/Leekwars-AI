"""Agregats par blocs et decision FIGEE avant les resultats.

Trois choses que ce module refuse de faire, parce qu'elles transforment une mesure en
illusion :

- **Fabriquer un intervalle de largeur nulle.** Si tous les `d` d'un lot sont identiques, la
  variance empirique vaut zero, et un test naif conclurait a une certitude parfaite. Des
  resultats identiques sur un lot ne prouvent pas l'equivalence de deux politiques : le
  verdict est NON CONCLUSIF.
- **Prolonger une confirmation jusqu'a ce qu'elle passe.** La taille est arretee avant de
  regarder, et une seule decision est prise. Regarder puis rallonger invalide le risque
  annonce.
- **Choisir apres coup le critere qui arrange.** Le controle eleveur et le controle de
  l'objectif pondere sont une CONJONCTION : les deux doivent passer.

La borne est une approximation de Student avec des degres de liberte de Welch-Satterthwaite,
affichee comme telle. Elle ne devient pas exacte parce qu'on l'a programmee.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

# Table de quantiles t unilateraux, interpolee en 1/nu. La bibliotheque standard n'a pas de
# fonction t inverse ; une table suffit ici et evite une dependance.
_TABLE = {
    0.05:  [(1, 6.314), (2, 2.920), (3, 2.353), (4, 2.132), (5, 2.015), (6, 1.943), (8, 1.860),
            (10, 1.812), (15, 1.753), (20, 1.725), (30, 1.697), (60, 1.671), (120, 1.658),
            (10**9, 1.645)],
    0.005: [(1, 63.657), (2, 9.925), (3, 5.841), (4, 4.604), (5, 4.032), (6, 3.707), (8, 3.355),
            (10, 3.169), (15, 2.947), (20, 2.845), (30, 2.750), (60, 2.660), (120, 2.617),
            (10**9, 2.576)],
    0.01:  [(1, 31.821), (2, 6.965), (3, 4.541), (4, 3.747), (5, 3.365), (6, 3.143), (8, 2.896),
            (10, 2.764), (15, 2.602), (20, 2.528), (30, 2.457), (60, 2.390), (120, 2.358),
            (10**9, 2.326)],
}


def quantile_t(alpha: float, nu: float) -> float:
    """Quantile unilateral 1-alpha. Interpolation lineaire en 1/nu, conservatrice aux bords."""
    if alpha not in _TABLE:
        raise ValueError("alpha non tabule : %r (disponibles : %s)" % (alpha, sorted(_TABLE)))
    table = _TABLE[alpha]
    if nu <= table[0][0]:
        return table[0][1]
    for (n1, t1), (n2, t2) in zip(table, table[1:]):
        if nu <= n2:
            x, x1, x2 = 1.0 / nu, 1.0 / n1, 1.0 / n2
            return t2 + (t1 - t2) * (x - x2) / (x1 - x2)
    return table[-1][1]


@dataclass
class Composante:
    """Un couple (format, adversaire) : la moyenne des `d`, leur variance et leur nombre."""
    format: str
    adversaire: str
    n: int
    moyenne: float
    variance: float          # variance EMPIRIQUE des d_i, ddof=1

    @property
    def variance_de_la_moyenne(self) -> float:
        return self.variance / self.n if self.n > 0 else float("inf")


def composante(format_nom: str, adversaire: str, d: Iterable[float]) -> Composante:
    d = list(d)
    n = len(d)
    if n == 0:
        return Composante(format_nom, adversaire, 0, 0.0, float("inf"))
    m = sum(d) / n
    v = sum((x - m) ** 2 for x in d) / (n - 1) if n > 1 else float("inf")
    return Composante(format_nom, adversaire, n, m, v)


def agreger_format(composantes: list[Composante]) -> tuple[float, float, list[float]]:
    """Delta_f et V_f pour un format, adversaires equipondees.

        Delta_f = somme_o( w_o * moyenne_o )        w_o = 1/len(adversaires)
        V_f     = somme_o( w_o^2 * variance_o / n_o )

    Rend aussi les composantes a_k = w^2 * var/n, dont Welch-Satterthwaite a besoin.
    """
    if not composantes:
        return 0.0, float("inf"), []
    w = 1.0 / len(composantes)
    delta = sum(w * c.moyenne for c in composantes)
    a = [w * w * c.variance_de_la_moyenne for c in composantes]
    return delta, sum(a), a


def borne_basse(estimation: float, a: list[float], n: list[int], alpha: float) -> tuple[float | None, float | None]:
    """Borne inferieure unilaterale, degres de liberte de Welch-Satterthwaite.

        nu = (somme a_k)^2 / somme( a_k^2 / (n_k - 1) )

    Rend (None, None) quand le test ne peut pas conclure : un effectif sous deux blocs, une
    variance infinie, ou une variance TOTALEMENT nulle. Ce dernier cas est le piege : il
    donnerait une borne de largeur zero, donc une certitude fabriquee.
    """
    if any(x < 2 for x in n):
        return None, None
    if any(math.isinf(x) or math.isnan(x) for x in a):
        return None, None
    total = sum(a)
    if total <= 0.0:
        return None, None
    denom = sum((x * x) / (m - 1) for x, m in zip(a, n) if m > 1)
    if denom <= 0.0:
        return None, None
    nu = (total * total) / denom
    return estimation - quantile_t(alpha, nu) * math.sqrt(total), nu


@dataclass
class Decision:
    verdict: str                     # PROMOUVOIR | INDICATIF | INCONCLUSIF | INCOMPLET
    raisons: list[str]
    delta: dict[str, float]
    borne: dict[str, float | None]
    nu: dict[str, float | None]
    j: float
    borne_j: float | None
    lacunes: list[str] = field(default_factory=list)


def controler_couverture(couverture: dict[str, dict], formats_requis: Iterable[str]
                         ) -> list[str]:
    """Les LACUNES du protocole, format par format. Une seule suffit a rendre INCOMPLET.

    Il ne suffit pas que les blocs conservés soient individuellement complets : il faut que
    TOUS les blocs prevus, TOUS les adversaires prevus et TOUS les formats requis soient
    presents. Sans ce controle, quarante blocs prevus contre deux adversaires dont l'un ne
    rend rien laissaient vingt blocs et un adversaire — et un verdict PROMOUVOIR.
    """
    lacunes: list[str] = []
    for f in formats_requis:
        c = (couverture or {}).get(f)
        if c is None:
            lacunes.append("format requis absent du protocole execute : %s" % f)
            continue
        manquants = c.get("blocs_incomplets") or []
        if manquants:
            exemples = ", ".join(str(m.get("indice")) for m in manquants[:5])
            lacunes.append("%s : %d bloc(s) incomplet(s) sur %d (indices %s%s)"
                           % (f, len(manquants), c.get("blocs_attendus", 0), exemples,
                              ", ..." if len(manquants) > 5 else ""))
        sans = c.get("adversaires_sans_donnees") or []
        if sans:
            lacunes.append("%s : aucun resultat contre %s" % (f, ", ".join(sans)))
        if not c.get("blocs_attendus"):
            lacunes.append("%s : aucun bloc prevu" % f)
    return lacunes


def decider(par_format: dict[str, list[Composante]], poids: dict[str, float],
            planchers: dict[str, float], alpha: float, gain_minimal: float,
            formats_requis: Iterable[str] = ("farmer",),
            couverture: dict[str, dict] | None = None,
            promouvable: bool = True, motif_non_promouvable: str = "") -> Decision:
    """Applique les conditions de promotion, toutes obligatoires et evaluees ensemble.

    `couverture` vient de l'orchestrateur et decrit ce qui a REELLEMENT ete joue. Une lacune
    donne INCOMPLET, jamais un verdict statistique sur l'echantillon survivant.

    `promouvable` est faux pour un lot technique — tailles imposees a la main, etape de crible,
    protocole non fige. Un tel lot peut rendre INDICATIF, jamais PROMOUVOIR.
    """
    formats_requis = list(formats_requis)
    raisons: list[str] = []
    lacunes = controler_couverture(couverture, formats_requis) if couverture is not None else []
    delta: dict[str, float] = {}
    a_par_format: dict[str, list[float]] = {}
    n_par_format: dict[str, list[int]] = {}
    borne: dict[str, float | None] = {}
    nu: dict[str, float | None] = {}

    for f, comps in par_format.items():
        d, _v, a = agreger_format(comps)
        delta[f] = d
        a_par_format[f] = a
        n_par_format[f] = [c.n for c in comps]
        b, df = borne_basse(d, a, n_par_format[f], alpha)
        borne[f] = b
        nu[f] = df

    for f in formats_requis:
        if f not in delta:
            lacunes.append("format requis absent des resultats : %s" % f)
    if lacunes:
        return Decision("INCOMPLET",
                        ["protocole incomplet : aucune promotion possible."] + lacunes,
                        delta, borne, nu, 0.0, None, lacunes)

    # J et sa variance : les poids s'appliquent aux composantes, pas aux nombres de combats.
    j = sum(poids.get(f, 0.0) * delta[f] for f in delta)
    a_j = [poids.get(f, 0.0) ** 2 * x for f in delta for x in a_par_format[f]]
    n_j = [x for f in delta for x in n_par_format[f]]
    bj, _nuj = borne_basse(j, a_j, n_j, alpha)

    if borne.get("farmer") is None or bj is None:
        raisons.append("test non concluant : effectif insuffisant ou variance nulle sur un lot. "
                       "Des resultats identiques ne prouvent pas l'equivalence.")
        return Decision("INCONCLUSIF", raisons, delta, borne, nu, j, bj, lacunes)

    ok = True
    if borne["farmer"] <= 0:
        ok = False
        raisons.append("borne basse eleveur non strictement positive (%.4f)" % borne["farmer"])
    if bj <= 0:
        ok = False
        raisons.append("borne basse de l'objectif pondere non strictement positive (%.4f)" % bj)
    if delta["farmer"] < gain_minimal:
        ok = False
        raisons.append("gain moyen eleveur sous le seuil pratique (%.4f < %.4f)"
                       % (delta["farmer"], gain_minimal))
    for f, plancher in planchers.items():
        if f in delta and delta[f] < plancher:
            ok = False
            raisons.append("moyenne %s sous son plancher empirique (%.4f < %.4f)"
                           % (f, delta[f], plancher))

    if ok:
        raisons.append("toutes les conditions statistiques sont remplies ; la borne reste une "
                       "approximation de Student et ne certifie pas une superiorite sur tout "
                       "le jeu.")
        if not promouvable:
            raisons.append("lot NON PROMOUVABLE : %s"
                           % (motif_non_promouvable or "protocole non fige"))
            return Decision("INDICATIF", raisons, delta, borne, nu, j, bj, lacunes)
        return Decision("PROMOUVOIR", raisons, delta, borne, nu, j, bj, lacunes)
    return Decision("INCONCLUSIF", raisons, delta, borne, nu, j, bj, lacunes)

# Base gloutonne LeekWars

Branche expérimentale `search-greedy-baseline`. Le main de production conserve
l’online evolution ; cette branche sert de témoin simple pour mesurer son intérêt.

## Recherche

[`ai/search/greedy`](ai/search/greedy) compare uniquement des actions isolées :

1. Capturer l’état réel et calculer son score.
2. Évaluer l’option de ne rien jouer, avec son meilleur placement et son danger.
3. Lister les actions jouables, simuler chaque action seule, puis calculer son
   meilleur placement final avec le même danger et les mêmes formules que main.
4. Garder le plus grand `scoreAprès − scoreInitial − pénalitéPlacement`.
5. Jouer cette action, recapturer le combat et recommencer.

Il n’y a ni arbre de combos, ni population, ni suite reprise entre deux casts.
Les actions locales sont financées avant une géométrie offensive potentiellement
froide. Les groupes et leurs origines, la simulation, les réserves d’opérations,
le placement final réel et les protections d’exécution sont ceux de main.
Une clôture tronquée ou non finançable est exclue du choix. Le maximum n’est garanti
que parmi les actions effectivement évaluées avant la limite de production.

Les sources sont sans extension ; importer `ai/` et utiliser [`ai/main`](ai/main).
La documentation des formules communes reste dans le
[main de référence](https://github.com/Traual/Leekwars-AI/tree/aa28d978712dcf48a7e84d6d91d16cb346aa492b).

## Validation avant comparaison

30 contrôles vérifient la restauration de l’état physique, le score incrémental
contre un recalcul complet, le placement/danger de chaque candidat et le choix
maximal incluant l’inaction. Les contrôles natifs vérifient également les miroirs
et l’identité des actions avec et sans observation externe des compteurs.

## Protocole 4v4 figé

100 combats par duel, 50 paires miroir communes aux trois duels :

- glouton contre beam ;
- glouton contre online evolution ;
- beam contre online evolution.

L’évolution est le main `aa28d97`. Le beam est le vrai moteur sauvegardé `3cb1123`,
avec seulement le correctif d’identités d’invocation commun aux trois versions.
Les builds, le moteur, les formules, les cartes de danger/soin et les budgets sont
figés. Aucun réglage ne change selon les résultats intermédiaires.

Les points et intervalles sont calculés par paires miroir. Le coût inclut les
cycles observés des VM propriétaires, callbacks des invocations compris ;
l’initialisation de VM et les compteurs natifs des seuls poireaux restent séparés.
Les profils de 32 premiers choix complètent les coûts observés sur les trajectoires
réelles, qui peuvent différer.

## Résultats du 3 novembre 2026

Les 300 combats sont terminés : zéro erreur, zéro avortement natif, zéro compteur
saturé. Le glouton mesuré est celui du commit `7b865d3` ; aucun réglage n’a changé
pendant la série. Un nul vaut un demi-point.

| Duel, points du premier | V / N / D | Points | IC apparié à 95 % |
|---|---:|---:|---:|
| Glouton / beam | 28 / 31 / 41 | 43,5 % | 39,0–47,5 % |
| Glouton / évolution | 24 / 36 / 40 | 42,0 % | 38,0–45,5 % |
| Beam / évolution | 38 / 23 / 39 | 49,5 % | 45,0–54,0 % |

Les intervalles rééchantillonnent les 50 paires miroir, avec leurs deux combats
ensemble. Ils ne sont pas corrigés pour les trois comparaisons et restent
conditionnels aux builds et au moteur figés.

Sur les 32 mêmes premiers choix, sans exécuter d’action réelle :

| Recherche | Opérations par choix, moyenne | Surcoût / glouton |
|---|---:|---:|
| Glouton | 5,469 M | référence |
| Beam | 7,147 M | +30,7 % |
| Évolution | 6,279 M | +14,8 % |

Les mêmes sept états à quatre cœurs n’ont pas de clôture validée dans les trois
modèles. Ils restent inclus dans ces moyennes. Les sondes utilisent quatre
plateaux fixes distincts des mondes de force, avec les échéances de planification
de production ; le plafond natif est levé seulement pour lire leur résultat.

En combat, les cycles de VM observés coûtent en moyenne 4,83–5,03 M pour le
glouton, contre 6,92–7,08 M pour les recherches de combos. Face au même adversaire
et sur les mêmes mondes, l’évolution gagne 7,0 points par rapport au glouton
contre le beam (IC : +1,0 à +14,0) ; le beam gagne 7,5 points par rapport au
glouton face à l’évolution (IC : +2,0 à +13,5).

Ces coûts de combat suivent des trajectoires différentes. Les lectures aux
frontières des tours peuvent omettre un callback immédiatement avant un reset ;
elles donnent des cycles observés, pas un compteur absolu de tout le calcul.
Les sondes à état identique complètent cette mesure, sans fournir un ratio causal
de victoires par opération.

**Conserver l’online evolution sur main.** Le glouton fournit une base plus simple
et moins coûteuse, mais les combos apportent un gain de force sur ce benchmark.
Beam et évolution ne sont pas départagés ; cette série ne justifie pas un retour
au beam. Main et la branche de sauvegarde restent inchangés.

Les scénarios, résultats bruts, empreintes, contrôles et rapport complet restent
dans le chantier local `work/greedy-comparison-20261103`, hors du dépôt de l’IA.

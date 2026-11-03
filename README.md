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
réelles, qui peuvent différer. Les résultats seront ajoutés après les 300 combats.

# Combos par case — prototype expérimental

Branche `search-cell-combos`. Main conserve l’online evolution.

## Recherche

1. Scorer exactement les actions initiales, avec le générateur commun.
2. Garder un départ par case représentante : l’action au meilleur gain brut.
3. Depuis chaque départ, construire une suite gloutonne sans déplacement volontaire
   supplémentaire. Réévaluer les actions depuis l’état obtenu après chaque cast.
4. Scorer chaque préfixe visité avec le placement final et le danger complets.
5. Repartir du meilleur préfixe s’il améliore le score : une autre case et une autre
   suite deviennent possibles avec les PT/PM encore disponibles.
6. Jouer seulement la première action, puis recapturer et replanifier.

Les cases candidates sont les origines représentantes déjà proposées par le
catalogue commun ; toutes les cellules atteignables ne sont pas explorées.
Une inversion change la case physique des pas suivants. Les morts, cooldowns,
équipements et ressources sont relus après chaque simulation. Aucun score de
combos indépendants n’est simplement additionné.

La politique conserve un seul départ par case et un seul prolongement glouton.
Elle peut donc manquer un meilleur ordre d’actions. Tous les scores calculés
restent ceux des formules existantes ; une clôture incomplète n’est pas sélectionnée.
Le budget peut aussi couper la recherche avant toutes les cases ou transitions.

Il n’y a ni population, ni mutation, ni nouvelle limite de profondeur ou de PT.
Les réserves, le simulateur, les maps et l’exécution réelle sont ceux de main.
La suite du meilleur plan reste disponible pour la vidange de fin de budget.

Les sources sont sans extension ; importer `ai/` puis utiliser `ai/main`.
Le moteur de recherche est dans `ai/search/cell-combos`.

## Contrôles avant comparaison

467 contrôles passent : scores incrémentaux contre recalcul complet, restauration
physique, placement/danger, origines locales et légalité des ressources. Un cas
ciblé prouve qu’une transition entre cases augmente le score par rapport à la
variante sans transition ; un autre vérifie les origines après inversion.
Les miroirs natifs et l’observateur externe reproduisent les mêmes actions et
compteurs, sans modifier les budgets de combat.

Sur les 32 mêmes premiers choix : 7,333 M opérations en moyenne, contre 6,279 M
pour l’online evolution. Sept états à quatre cœurs restent sans clôture validée,
comme les autres moteurs. La variante sans transition coûte 7,324 M ; aucun
changement de case supplémentaire n’améliore la suite retenue sur ces 32 états.

## Comparaison 4v4 figée

100 combats frais contre main `aa28d97`, 50 paires miroir, budgets natifs,
seed 2026110302. Aucun réglage ni arrêt selon les résultats intermédiaires.
Les résultats seront ajoutés après la série complète. Aucune promotion automatique.
Les reçus et résultats bruts restent dans le chantier local
`work/cell-combos-20261103`, hors du dépôt de l’IA.

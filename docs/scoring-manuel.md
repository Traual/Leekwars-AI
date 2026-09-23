# Scoring manuel

Trois fichiers dans `New_AI/Scoring` :

- `Coefficients.leek` : une fonction par statistique, essentiellement des constantes de départ.
- `Scoring.leek` : somme des `statistique × coefficient`. Un mort vaut zéro ; une entité vivante garde au moins un point.
- `Placement.leek` : distance aux alliés, distance aux ennemis, exposition au danger. Même poids pour toutes les entités.

Le score du plateau est la somme alliée moins la somme adverse. Le gain d'une action est la différence entre le score après et avant. La présence vaut 100 pour tout vivant, sans distinction de kit ou d'invocation. Les statistiques de base sont incluses : invoquer ou relever une entité apporte aussi la valeur de ses caractéristiques.

Pour commencer, modifier simplement les `return` de `Coefficients.leek`. Vie : 1 ; vie max : 0,5 ; caractéristiques : 1 ; PT : 30 ; PM : 15 ; bouclier absolu : 1 ; bouclier relatif et renvoi : 10. Poison, contrecoup et soin continu lisent les agrégats de ticks restants, avec -0,5, -0,75 et +0,5. Seuls quelques cas mécaniques sont gardés : poison nul sous invincibilité, soin nul sous UNHEALABLE, PM sans valeur si statique ou enraciné.

Le placement est soustrait seulement en fin de suite, par `FinalCell`, puis utilisé pour le déplacement réel. Ses trois fonctions sont dans `Placement.leek` : distance cible 3 aux alliés, 5 aux ennemis, exposition `0.05 × max(0, net)`. Il emploie les cartes existantes avec les mêmes réserves d'opérations.

Deux règles pour les futures modifications : les fonctions de coefficient doivent rester pures et ne lire que leur argument `s` (le calcul incrémental ne relit que les entités changées) ; les coûts de placement doivent rester positifs ou nuls (le reclassement terminal utilise cette propriété). Si le placement se met à lire d'autres statistiques, adapter aussi ses clés de cache.

Le majorant analytique d'action a été supprimé : pas de seconde formule à synchroniser quand on change un coefficient. Les heuristiques de recherche et les limites d'opérations restent en place ; la recherche n'est pas exhaustive. Les dégâts initialement annulés par les boucliers sont resimulés sous budget après un éventuel débuff.

Aucune pondération d'importance, aucun profil de kit, aucun réseau ni préparation des poids. Les anciennes sondes de décision basées sur `DIAG_*` et `Weights.leek` concernent les anciens commits ; pour cette version, utiliser `validation/scoring_simple`.

Ces valeurs sont un point de départ lisible, pas des coefficients optimisés ni une promesse de battre `main`.

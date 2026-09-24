# IA LeekWars

IA orientée simulation : elle cherche une suite d’actions, joue la première, puis replanifie depuis l’état réel du combat.

Ce dépôt contient uniquement **`New_AI/`** et ce README, pour importer l’IA dans LeekWars sans embarquer les outils de développement.

## Utilisation

Le point d’entrée est [`New_AI/Main.leek`](New_AI/Main.leek). Importer le dossier `New_AI` en conservant son arborescence et les chemins des `include`.

Les modifications courantes du scoring se font sur **`main`**.

## Modifier le scoring

Quatre fichiers :

- [`Coefficients.leek`](New_AI/Scoring/Coefficients.leek) : une fonction simple par statistique.
- [`Equipment.leek`](New_AI/Scoring/Equipment.leek) : utilitaires de rendement de l'équipement, mis en cache par entité et par tour.
- [`Scoring.leek`](New_AI/Scoring/Scoring.leek) : somme des statistiques multipliées par leur coefficient, alliés moins adversaires.
- [`Placement.leek`](New_AI/Scoring/Placement.leek) : distances et exposition au danger, soustraites à la fin des suites d’actions.

Une entité morte vaut zéro. Chaque vivant reçoit le même bonus de présence, sans score d’importance. Les coefficients sont des valeurs de départ à régler manuellement.

Le coefficient de force vaut **0,1 × le meilleur rendement physique de l'inventaire** (dégâts de base moyens par PT). Ainsi, 40–60 dégâts pour 5 PT donnent un coefficient de 1 ; sans dégâts physiques, il vaut 0. Le facteur 0,1 correspond à 10 PT futurs de référence divisés par 100, et se règle dans `Coefficients.strength`. Il valorise la force conservée après la suite d'actions : les dégâts effectivement simulés sont déjà comptés dans la vie. La contribution utilise `max(0, force)`, comme le moteur pour les dégâts.

Ce rendement considère une cible au centre de la zone, au coût nominal de l'item ; il ignore cooldowns, limites d'usage, portée, équipement de l'arme, protections et multiplicateurs du porteur. Les lignes d'un même cast s'additionnent, mais pas les armes entre elles. Puces et armes sont couvertes ; Châtiment, poison, nova et dégâts sur soi sont exclus. C'est une estimation simple du potentiel futur, pas une prévision de dégâts réalisables au tour courant.

Les fonctions de coefficient doivent ne dépendre que de leur état `stat` et du catalogue immuable pour conserver le calcul incrémental. Le cache d'équipement suppose l'inventaire constant au sein d'une recherche. Les pénalités de placement doivent rester positives ou nulles ; si leurs entrées changent, adapter aussi les clés de cache.

## Documentation et outils

Le dépôt séparé **[Leekwars-AI-tools](https://github.com/Traual/Leekwars-AI-tools)** conserve :

- le [guide du scoring](https://github.com/Traual/Leekwars-AI-tools/blob/main/docs/scoring-manuel.md) ;
- l’[architecture et les limites documentées](https://github.com/Traual/Leekwars-AI-tools/blob/main/docs/architecture-8779f63.md) ;
- le [harnais d’entraînement](https://github.com/Traual/Leekwars-AI-tools/tree/main/training), les builds et les rapports ;
- les [validations du scoring simple](https://github.com/Traual/Leekwars-AI-tools/tree/main/validation/scoring_simple).

Ces outils restent hors de l’import LeekWars. Leur README explique comment préparer un espace de tests séparé.

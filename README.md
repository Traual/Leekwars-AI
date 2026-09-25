# IA LeekWars

IA orientée simulation : elle cherche une suite d’actions, joue la première, puis replanifie depuis l’état réel du combat.

Ce dépôt contient uniquement **`New_AI/`** et ce README, pour importer l’IA dans LeekWars sans embarquer les outils de développement.

## Utilisation

Le point d’entrée est [`New_AI/Main.leek`](New_AI/Main.leek). Importer le dossier `New_AI` en conservant son arborescence et les chemins des `include`.

Les modifications courantes du scoring se font sur **`main`**.

## Modifier le scoring

Six fichiers :

- [`Coefficients.leek`](New_AI/Scoring/Coefficients.leek) : une fonction simple par statistique.
- [`Equipment.leek`](New_AI/Scoring/Equipment.leek) : utilitaires de rendement de l'équipement, mis en cache par entité et par tour.
- [`StateConstants.leek`](New_AI/Scoring/StateConstants.leek) : proportions de cibles adverses, en cache par `BaseHash`.
- [`BuffDuration.leek`](New_AI/Scoring/BuffDuration.leek) : multiplicateur de durée et agrégats des buffs de force.
- [`Scoring.leek`](New_AI/Scoring/Scoring.leek) : somme des statistiques multipliées par leur coefficient, alliés moins adversaires.
- [`Placement.leek`](New_AI/Scoring/Placement.leek) : distances et exposition au danger, soustraites à la fin des suites d’actions.

Une entité morte vaut zéro. Chaque vivant reçoit le même bonus de présence, sans score d’importance. Les coefficients sont des valeurs de départ à régler manuellement.

Le coefficient de force vaut **0,1 × (rendement sur poireaux × poids poireaux + rendement sur invocations × poids invocations)**. Chaque rendement est le meilleur ratio dégâts physiques de base moyens / PT de l'inventaire pour ce type de cible. Ainsi, 40–60 dégâts pour 5 PT sur les deux types donnent un coefficient de 1 ; sans dégâts physiques, il vaut 0. Le facteur 0,1 correspond à 10 PT de référence par tour valorisé divisés par 100, et se règle dans `Coefficients.strength`. Les dégâts effectivement simulés sont déjà comptés dans la vie.

Pour les buffs additifs de force à durée positive, le multiplicateur vaut **durée restante** sur une autre entité et **durée restante − 1 + 0,01** sur Me (le porteur, pas forcément le lanceur). Sans entrave, un buff de +100 pour un tour ajoute donc `100 × coefForce` ailleurs et `1 × coefForce` sur Me ; pour trois tours, respectivement `300 × coefForce` et `201 × coefForce`. La règle utilise la durée brute `eff[3]`, sans plafond ; les effets permanents ou instantanés gardent leur valeur ordinaire. Les autres statistiques ne sont pas encore pondérées par durée.

Deux scalaires de l'état conservent la somme brute des buffs de force et leur somme pondérée. Ils sont initialisés dans la passe d'import existante et maintenus à la pose, fusion, réduction, remplacement et suppression des effets ; la résurrection les remet à zéro. Le scoring remplace la partie buff par sa valeur pondérée, sans multiplier le delta entier de l'action et sans parcourir les effets à chaque nœud. Si une entrave fait passer la force sous zéro, seule la partie positive est valorisée ; les durées des buffs qui la composent sont alors pondérées au prorata de leurs valeurs.

Les poids sont les proportions exactes parmi les adversaires vivants : 60 % d'invocations donnent 40 % poireaux / 60 % invocations ; aucune invocation donne 100 % poireaux / 0 % invocations. Sans aucun adversaire vivant, les deux poids valent zéro. `StateConstants.leek` calcule ces proportions par camp (BR inclus) et conserve le résultat par `BaseHash` au sein du tour. Le BFS prend le contexte de sa racine avant tout score, résurrection incluse, puis le fige pour tout l'arbre. Une mort simulée ne revalorise pas les porteurs non modifiés ; une action réelle fournit le contexte de la nouvelle racine à la recherche suivante.

Ce rendement considère une cible au centre de la zone, au coût nominal de l'item ; il ignore cooldowns, limites d'usage, portée, équipement de l'arme, protections et multiplicateurs du porteur. Les lignes d'un même cast s'additionnent, mais pas les armes entre elles. Puces et armes sont couvertes ; Châtiment, poison, nova et dégâts sur soi sont exclus. C'est une estimation simple du potentiel futur, pas une prévision de dégâts réalisables au tour courant.

Les fonctions de coefficient ne doivent dépendre que de leur état `stat`, du catalogue immuable et du contexte figé à la racine pour conserver le calcul incrémental. Le cache d'équipement stocke un `StrengthYield` sans pondération, par ID et classe réelle/virtuelle, et suppose l'inventaire constant au sein d'une recherche. Le contexte `ScoringStateConstants` contient un `TargetWeights` par camp ; rendements et poids exposent les champs nommés `LEEKS` et `SUMMONS`. Son cache par `BaseHash` s'utilise sur les racines réelles, où Me est vivant, et est vidé au début du tour de chaque joueur. Les caches de scores restent locaux à la recherche. Les pénalités de placement doivent rester positives ou nulles ; si leurs entrées changent, adapter aussi les clés de cache.

## Documentation et outils

Le dépôt séparé **[Leekwars-AI-tools](https://github.com/Traual/Leekwars-AI-tools)** conserve :

- le [guide du scoring](https://github.com/Traual/Leekwars-AI-tools/blob/main/docs/scoring-manuel.md) ;
- l’[architecture et les limites documentées](https://github.com/Traual/Leekwars-AI-tools/blob/main/docs/architecture-8779f63.md) ;
- le [harnais d’entraînement](https://github.com/Traual/Leekwars-AI-tools/tree/main/training), les builds et les rapports ;
- les [validations du scoring simple](https://github.com/Traual/Leekwars-AI-tools/tree/main/validation/scoring_simple).

Ces outils restent hors de l’import LeekWars. Leur README explique comment préparer un espace de tests séparé.

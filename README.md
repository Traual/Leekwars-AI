# IA LeekWars

IA orientée simulation : elle cherche une suite d’actions, joue la première, puis replanifie depuis l’état réel du combat.

Ce dépôt contient uniquement **`New_AI/`** et ce README, pour importer l’IA dans LeekWars sans embarquer les outils de développement.

## Utilisation

Le point d’entrée est [`New_AI/Main.leek`](New_AI/Main.leek). Importer le dossier `New_AI` en conservant son arborescence et les chemins des `include`.

Les modifications courantes du scoring se font sur **`main`**.

## Modifier le scoring

Sept fichiers :

- [`Coefficients.leek`](New_AI/Scoring/Coefficients.leek) : une fonction simple par statistique.
- [`Equipment.leek`](New_AI/Scoring/Equipment.leek) : utilitaires de rendement de l'équipement, mis en cache par entité et par tour.
- [`StateConstants.leek`](New_AI/Scoring/StateConstants.leek) : proportions de cibles adverses/alliées en cache par `BaseHash`, et échelle du bonus critique.
- [`BuffDuration.leek`](New_AI/Scoring/BuffDuration.leek) : multiplicateur de durée et agrégats des buffs de force et d'agilité.
- [`Scoring.leek`](New_AI/Scoring/Scoring.leek) : somme des statistiques multipliées par leur coefficient, alliés moins adversaires.
- [`Debug.leek`](New_AI/Scoring/Debug.leek) : coefficients de chaque vivant au début du tour, avec une pause après toute la liste. `ScoringDebug.ENABLED = false` désactive l'affichage et les pauses ; les valeurs affichées sont les coefficients avant pondération par durée.
- [`Placement.leek`](New_AI/Scoring/Placement.leek) : distances et exposition au danger, soustraites à la fin des suites d’actions.

Une entité morte vaut zéro. Chaque vivant reçoit le même bonus de présence, sans score d’importance. Les coefficients sont des valeurs de départ à régler manuellement.

Le coefficient de force utilise des **paliers sur la force courante** : base de 0,2, puis +0,2 au-dessus de 0, +0,3 dès 200, +0,3 dès 400, +0,2 dès 600, +0,2 dès 800 et +0,1 dès 1 000. Les coefficients cumulés sont donc 0,2, 0,4, 0,7, 1, 1,2, 1,4 et 1,5. Si `Equipment.strengthYield` est nul sur poireaux et invocations, le coefficient vaut toujours zéro. Le rendement et son cache sont conservés, mais leur amplitude et les proportions de cibles ne pondèrent plus ce coefficient. Les dégâts effectivement simulés sont déjà comptés dans la vie.

Pour les buffs additifs de force, d'agilité, de sagesse, de résistance et de magie à durée positive, le multiplicateur vaut **durée restante** sur une autre entité et **durée restante − 1 + 0,01** sur Me (le porteur, pas forcément le lanceur). Sans entrave et à coefficient inchangé, un buff de +100 force pour un tour ajoute donc `100 × coefForce` ailleurs et `1 × coefForce` sur Me ; pour trois tours, respectivement `300 × coefForce` et `201 × coefForce`. Si un palier de force est franchi, le nouveau coefficient s'applique à toute la force valorisée de l'état après l'action. La règle utilise la durée brute `eff[3]`, sans plafond ; les effets permanents ou instantanés gardent leur valeur ordinaire. La sagesse a le même plancher à −100 que l'agilité (soin et vitalité utilisent `max(0, 1 + sagesse / 100)`) ; la résistance n'en a aucun, les boucliers la prenant telle quelle ; la magie est planchée à 0 comme la force. Les autres statistiques ne sont pas encore pondérées par durée.

Deux scalaires par caractéristique conservent la somme brute des buffs et leur somme pondérée. Ils sont initialisés dans la passe d'import existante et maintenus à la pose, fusion, réduction, remplacement et suppression des effets ; la résurrection les remet à zéro. Les buffs de Saut et les passifs d'armes passent par les mêmes points d'enregistrement. Le scoring remplace la partie buff par sa valeur pondérée, sans multiplier le delta entier de l'action et sans parcourir les effets à chaque nœud. Si une entrave fait passer la force sous zéro, seule la partie positive est valorisée ; les durées des buffs qui la composent sont alors pondérées au prorata de leurs valeurs.

Le coefficient d'agilité part de la **base critique** décrite ci-dessous. Si `Equipment.damageReturnYield` est nul sur poireaux et invocations, il conserve uniquement cette base. Sinon, il ajoute les mêmes paliers que la force, sur l'agilité courante : +0,2 au-dessus de 0, +0,3 dès 200, +0,3 dès 400, +0,2 dès 600, +0,2 dès 800 et +0,1 dès 1 000. Le rendement sert seulement à détecter un renvoi lançable ; sa valeur et les proportions d'alliés ne pondèrent plus le coefficient. Le debug affiche le coefficient total et rappelle la base critique déjà incluse.

Le coefficient de sagesse part d'une base de 0,2, puis ajoute +0,2 au-dessus de 0, +0,3 dès 200, +0,3 dès 400, puis +0,1 dès 500, 600 et 700. Il vaut zéro si `Equipment.healYield` (soin, instantané ou par tour) et `Equipment.boostMaxLifeYield` (vitalité), les deux seuls effets multipliés par la sagesse, sont nuls sur poireaux et invocations. Chaque rendement compte ses effets lancés sur un allié ou sur soi ; le soin brut, indépendant de la sagesse, n'y entre pas.

Le coefficient de résistance reprend les paliers de la sagesse, sur la résistance courante. Il vaut zéro si `Equipment.absoluteShieldYield` (en PV) et `Equipment.relativeShieldYield` (en %), les deux seuls effets multipliés par la résistance, sont nuls sur poireaux et invocations. Chaque rendement compte ses effets lancés sur un allié ou sur soi ; les boucliers bruts n'y entrent pas.

Le coefficient de magie reprend les paliers de la force, sur la magie courante. Il vaut zéro si aucun effet multiplié par la magie n'a de rendement, sur poireaux et invocations : `Equipment.poisonYield` et les six entraves (`shackleStrengthYield`, `shackleMagicYield`, `shackleAgilityYield`, `shackleWisdomYield`, `shackleTpYield`, `shackleMpYield`), tous lancés sur un ennemi. Une seule suffit : un allié qui n'empoisonne pas mais entrave la force garde un coefficient. `Equipment.canUseMagic` met ce verdict en cache pour ne pas relire les sept rendements à chaque nœud.

Les coefficients de force, de sagesse, de résistance, de magie et la seule partie renvoi du coefficient d'agilité sont ensuite multipliés selon `TOTAL_TP`, chacun par sa propre fonction (`strengthTpMultiplier`, `wisdomTpMultiplier`, `resistanceTpMultiplier`, `magicTpMultiplier`, `agilityTpMultiplier`) pour rester réglables séparément. Le facteur n'est pas cumulé : le premier palier atteint l'emporte. Chaque barème se lit directement dans `Coefficients.leek`. Aucune distance n'entre dans ces coefficients ; elle reste traitée par le placement final.

Le scoring soustrait cette base du coefficient total pour valoriser séparément le renvoi et le critique, sans compter deux fois ce dernier. La contribution de renvoi conserve le plancher à **−100** d'agilité ; le critique conserve sa plage **[0, 1000]**. Un franchissement de palier modifie le coefficient de toute l'agilité valorisée pour le renvoi, comme pour la force.

**Critique :** les points d'agilité utiles sont limités à **[0, 1000]**, conformément à la probabilité moteur `agilité / 1000`, puis pondérés par la durée des buffs. Leur coefficient vaut `TOTAL_TP / 100 × (1 + max(0, meilleure caractéristique de base) / 100)`, avec le maximum de force, magie, sagesse, science et résistance. Un attaquant ou un soutien plus puissant valorise donc davantage les critiques ; un buff de force en branche ne revalorise pas aussi tout le stock d'agilité. L'échelle est mémorisée sur l'entité et copiée avec ses clones, car `BASE_STATS` reste immuable. Le facteur `TOTAL_TP / 100` suit la capacité courante en PT ; les multiplicateurs à paliers du renvoi ne lui sont pas appliqués. À titre d'exemple, avec une meilleure base de 400 et 14 PT, le coefficient critique vaut 0,7. Le renvoi et le critique suivent tous deux la même règle de durée ; en cas de buffs de durées différentes franchissant un plancher ou plafond, la partie utile est répartie au prorata de leurs valeurs.

Les poids sont les proportions exactes parmi les adversaires vivants : 60 % d'invocations donnent 40 % poireaux / 60 % invocations ; aucune invocation donne 100 % poireaux / 0 % invocations. Sans aucun adversaire vivant, les deux poids valent zéro. `StateConstants.leek` calcule ces proportions par camp (BR inclus) et conserve le résultat par `BaseHash` au sein du tour. Le BFS prend le contexte de sa racine avant tout score, résurrection incluse, puis le fige pour tout l'arbre. Une mort simulée ne revalorise pas les porteurs non modifiés ; une action réelle fournit le contexte de la nouvelle racine à la recherche suivante.

Ce rendement considère une cible au centre de la zone, au coût nominal de l'item ; il ignore cooldowns, limites d'usage, portée, équipement de l'arme, protections et multiplicateurs du porteur. Les lignes d'un même cast s'additionnent, mais pas les armes entre elles. Puces et armes sont couvertes ; Châtiment, poison, nova et dégâts sur soi sont exclus. C'est une estimation simple du potentiel futur, pas une prévision de dégâts réalisables au tour courant.

Les fonctions de coefficient ne doivent dépendre que de leur état `stat`, du catalogue immuable et du contexte figé à la racine pour conserver le calcul incrémental. Les caches d'équipement stockent des `EquipmentYield` sans pondération, par ID et classe réelle/virtuelle, et supposent l'inventaire constant au sein d'une recherche. Le contexte `ScoringStateConstants` contient un `TargetWeights` adverse et allié par camp ; rendements et poids exposent les champs nommés `LEEKS` et `SUMMONS`. Son cache par `BaseHash` s'utilise sur les racines réelles, où Me est vivant, et est vidé au début du tour de chaque joueur. Les caches de scores restent locaux à la recherche. Les pénalités de placement doivent rester positives ou nulles ; si leurs entrées changent, adapter aussi les clés de cache.

## Documentation et outils

Le dépôt séparé **[Leekwars-AI-tools](https://github.com/Traual/Leekwars-AI-tools)** conserve :

- le [guide du scoring](https://github.com/Traual/Leekwars-AI-tools/blob/main/docs/scoring-manuel.md) ;
- l’[architecture et les limites documentées](https://github.com/Traual/Leekwars-AI-tools/blob/main/docs/architecture-8779f63.md) ;
- le [harnais d’entraînement](https://github.com/Traual/Leekwars-AI-tools/tree/main/training), les builds et les rapports ;
- les [validations du scoring simple](https://github.com/Traual/Leekwars-AI-tools/tree/main/validation/scoring_simple).

Ces outils restent hors de l’import LeekWars. Leur README explique comment préparer un espace de tests séparé.

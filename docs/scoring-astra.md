# Scoring Astra — version initiale raisonnée

Branche `scoring-astra-20260921`, issue de `scoring-simple` à `3024424`.
Cette version est une proposition à examiner en combat, pas un champion promu.
Elle conserve le simulateur, le générateur, les builds et le protocole d'entraînement.

## Où intervenir

- [Weights.leek](../New_AI/Scoring/Weights.leek) : hypothèses stratégiques réglables, exprimées en PV.
- [Coefficients.leek](../New_AI/Scoring/Coefficients.leek) : une fonction par caractéristique, protections, effets périodiques et placement ; préparation du profil du kit.
- [Scoring.leek](../New_AI/Scoring/Scoring.leek) : somme des contributions, delta incrémental, résurrection, victoire et majorant.
- [HistoricalWeights.leek](../New_AI/Scoring/HistoricalWeights.leek) : préparation à chaque replanification, distances visées et invalidation des caches concernés.
- [FinalCell.leek](../New_AI/User/FinalCell.leek) : application terminale de la pénalité, au même endroit qu'avant.

`SCORE_EXPLAIN = true` permet de lire les contributions d'une décision ; `DIAG_TURN` et `DIAG_ENTITY` ciblent le diagnostic existant. Ils sont désactivés dans la version livrée.

## Forme du score

La valeur reste une somme : vie × coefficient, vie maximale × coefficient, présence, puis chaque caractéristique × son coefficient. Pour les caractéristiques et les capacités PT/PM, on utilise **la différence à la vraie base**. La valeur intrinsèque du kit est déjà représentée par la présence ; une résurrection ne recrée donc pas artificiellement une deuxième valeur de toutes les statistiques de base.

Les profils sont construits depuis les inventaires, les vraies bases et le contexte réel, puis **figés pendant toute une recherche**. Cette contrainte garantit que modifier un allié ne change pas silencieusement le score des autres entités : le delta des entités clonées reste exactement égal au recalcul complet. Les nouvelles invocations ont un cache séparé incluant leur base, notamment les différences dues au critique.

Le potentiel futur utilise le meilleur item pertinent, son coût, sa limite d'utilisations et son cooldown nominal. Il ne résout pas une seconde recherche. Les coefficients valent zéro lorsqu'aucun effet du kit ne profite de la caractéristique. Les profils combinent des dérivées des formules du moteur avec des hypothèses stratégiques explicites : `SCORE_FUTURE_TURNS = 0.65`, par exemple, **n'est pas une règle du jeu**.

Une entité morte vaut zéro. Une entité vivante garde un petit plancher positif même si elle est très entravée. Le dernier adversaire éliminé déclenche un bonus terminal de 10 millions ; une invocation encore vivante empêche ce bonus. Il est désactivé pour les formats spéciaux dont les règles de fin diffèrent.

## Relecture du générateur, coefficient par coefficient

Source examinée : [leek-wars-generator, révision 9931d291](https://github.com/leek-wars/leek-wars-generator/tree/9931d29188c941ffdc31ca2a4cb67bfe7dfccda4/src/main/java/com/leekwars/generator). Les chemins ci-dessous sont relatifs à ce répertoire. Les empreintes du moteur réellement exécuté sont dans [provenance.json](../validation/scoring_astra/results/provenance.json). La révision source et l'empreinte du JAR sont enregistrées séparément : ce travail ne reconstruit pas le moteur.

| Fonction | Mécanismes examinés | Traduction dans le score et limite principale |
| --- | --- | --- |
| `life` | `effect/EffectLifeDamage.java`, soins, vol de vie et érosion | 1 PV vaut au moins 1 ; supplément pour les dégâts alimentés par la vie. Leur potentiel est ancré sur une réserve de référence, sans recalculer toutes les attaques à chaque nœud. |
| `totalLife` | `EffectVitality`, `EffectNovaVitality`, `EffectNovaDamage` | La capacité de soin future a une petite valeur. Vitalité ajoute vie et vie max ; nova vitalité ajoute seulement la capacité. Les effets réels restent ceux du simulateur. |
| `presence` | `state/Team.isDead`, `state/State.isFinished`, invocation/résurrection | Petit bonus vivant + fraction plafonnée de production du kit, sans somme supplémentaire des stats de base. Pas d'estimation exacte du nombre de tours restant à vivre. |
| `strength` | `EffectDamage` | Dérivée des dégâts physiques, plancher à zéro, armure et invincibilité adverses de référence. Aucun crédit de force pour un pur soigneur. |
| `wisdom` | `EffectHeal`, `EffectRawHeal`, `EffectVitality`, vol de vie dans `EffectDamage` | Sépare soins/vitalité et vol de vie. Ce dernier dépend des dégâts réellement retirés et ne profite pas d'une sagesse négative. Les besoins futurs en soin restent estimés. |
| `agility` | `state/State.generateCritical`, `EffectDamageReturn`, `state/Entity.onCritical`, invocation | Critiques saturés à 1 000 ; renvoi séparé, sans ce plafond. Les passifs de soin critique de toutes les armes sont pris en compte. Le critique d'invocation ajoute 20 %, pas 30 %. |
| `resistance` | `EffectAbsoluteShield`, `EffectRelativeShield`, variantes `Raw` | Dérivée des protections créables par le kit, saturée lorsque le hit de référence est annulé. Aucun facteur résistance fictif sur les effets bruts. |
| `science` | Buffs, `EffectAftereffect`, `EffectNovaDamage`, `EffectNovaVitality` | Sépare nova (science plancherée à zéro) et effets proportionnels à `1 + SCI/100`. La conversion future des buffs vers leur utilité utilise encore un tarif générique, pas le meilleur destinataire exact. |
| `magic` | `EffectPoison` et entraves | Poison/entraves selon la magie positive ; pas de crédit pour un kit sans ces effets. Le coût futur d'une entrave utilise un tarif de secours. |
| `power` | `EffectDamage`, `EffectLifeDamage`, `EffectPoison`, `EffectNovaDamage` | Crédit sur ces canaux, aucun facteur puissance ajouté aux soins, boucliers ou contrecoups. Les interactions de deux buffs simultanés restent linéarisées autour des bases. |
| `totalTP` | Coûts, limites d'utilisations et cooldowns du catalogue | Prix tiré de la production du kit et plafonné. C'est une estimation marginale de capacité future, pas une division naïve des seuls dégâts passés par les PT dépensés. |
| `totalMP` | `state/State` : marche, ROOTED, STATIC | Tarif lié au kit ; zéro gain de capacité de marche sous ROOTED/STATIC. Le placement effectif est évalué à part, à la clôture. |
| `relativeShield` | `EffectDamage`, `EffectLifeDamage` | Économie de dégâts sur la pression physique de référence ; saturation et vulnérabilités conservées. Le pourcentage au-delà du blocage n'apporte plus de valeur. |
| `absoluteShield` | Même source, application **par impact** | Complément après le relatif, sans double compte de protection. Les impacts multiples restent distincts. Sous INVINCIBLE, une ABS négative peut encore laisser passer `EffectLifeDamage`, dont le clamp précède l'ABS. |
| `damageReturn` | Renvoi calculé dans `EffectDamage` avant les boucliers de la cible | Pression brute, part adverse vulnérable, plafond de vie adverse. L'adversaire n'est pas supposé s'acharner avec certitude sur le renvoi. |
| `poison` | `EffectPoison.applyStartTurn` | Ticks utiles plafonnés par la vie ; zéro sous INVINCIBLE. Le prochain tick vaut davantage que la queue distante. |
| `aftereffect` | `EffectAftereffect.apply` et `applyStartTurn` | La pose teste INVINCIBLE, **les ticks déjà posés ne le retestent pas**. Ils restent donc un coût sous invincibilité. |
| `heal` | `EffectHeal`, `EffectRawHeal`, plafonds de vie | Zéro sous UNHEALABLE ; limite par le manque de vie projeté, avec une réserve modeste pour des dégâts futurs. Les soins ne valent pas automatiquement leur total brut. |
| `states` | `attack/EntityState`, contrôles d'état dans `state/State` et les effets | Crédit de protection INVINCIBLE, retrait des débouchés de vol de vie/soin critique sous UNHEALABLE, de critique d'invocation sous STERILE. Ce n'est pas une simulation temporelle complète des états. |
| `placement` | Portées, projections Delta/Heal existantes, déplacements et effets de zone | Prix du danger en PV, exposition moins diluée en solo, distances selon le kit et coût spatial remis dans la même unité. Aucun nouveau chargement de danger par nœud. |

Autres vérifications transversales :

- `fight/entity/EntityAI.getFeatureArray` expose **minimum et maximum**. Les JSON du moteur exposent minimum et amplitude. Le profil utilise bien `(min + max) / 2` sur les données API.
- Les effets bruts, le vol d'ABS et le vol de vie adossés à `previousEffectTotalValue` ne reçoivent pas un second facteur résistance/sagesse inventé.
- `state/Entity.onDirectDamage`, `onNovaDamage`, `onPoisonDamage`, `onMoved`, `onCritical` et les gains au kill ont été examinés. Les changements déjà produits sont valorisés via les statistiques simulées. À l'exception du soin critique, le profil **ne prédit pas une chaîne complète de futurs passifs**.
- Libération, désenvoûtement, vol de bouclier, superinfection et multiplication des statistiques sont valorisés par leur état résultant. Aucun bonus forfaitaire d'item n'est ajouté. Le registre d'effets et ses règles de fusion/irréductibilité restent dans le simulateur existant.
- Les approximations déjà acceptées dans les cartes de danger, notamment certains effets propres du saut/téléportation, restent inchangées.

## Placement et recherche

La pénalité reste appliquée aux clôtures terminales du BFS et au choix final de cellule, **pas au chargement initial pour figer un classement des actions**. Aucun changement fonctionnel dans `BFS.leek`.

L'exposition vaut `0.05 + 0.60 / nombre_alliés_vivants²`, multipliée par le coefficient de vie. C'est une hypothèse de concentration du feu : 0,65 seul, 0,0875 à quatre. La proximité alliée vise 3 cellules pour un kit de soutien, 4 sinon ; la distance ennemie dérive de la portée offensive, bornée entre 1 et 6. La géométrie conserve les règles de gravité, contagion et regroupement existantes. Le coût spatial est multiplié par 20 pour l'exprimer en points comparables aux PV.

Ces paramètres ne sont pas calibrés sur des combats du top. La distance ne résout pas une ligne de vue, l'exposition n'est pas une probabilité mesurée, et la population compte aussi les invocations. Les caches de placement sont invalidés lorsque le contexte qui entre désormais dans leur résultat change.

Le majorant ne borne que les dégâts directs pour lesquels l'ancien système disposait déjà d'une voie sûre. Il couvre les nouvelles pentes de valeur de vie/vie maximale et les plafonds périodiques. Kills, effets composés et cas non couverts rendent la sentinelle « simuler ». Les 71 actions finiment bornées du test d'intégration passent ; cela ne constitue pas une preuve exhaustive. Aucun audit de coupe coûteux n'est activé en production.

## Vérifications exécutées

Résultats détaillés, programmes de reproduction et observations : [validation/scoring_astra](../validation/scoring_astra/README.md).

| Vérification | Résultat |
| --- | --- |
| Propriétés mécaniques | 17 contrôles, dont 204 mutations autour des seuils/négatifs : tous passent. |
| Score incrémental | 136 mutations simples et 466 nœuds réels/chargés : aucun écart avec le recalcul complet. |
| Mort, résurrection, placement | Contrôles d'intégration passants, dont la résurrection par la même transition que le simulateur. |
| Majorant | 31 + 40 actions finiment bornées, aucune violation observée ; les autres rendent la sentinelle. |
| Comptabilité de l'oracle | 6 tests Python : identité des entités, mort avant l'IA, racines identiques, exclusion des paires invalides/incomplètes. |
| Situations tactiques | 120 cas, 32 graines appariées, 2 choix par cas : 7 680 microcombats natifs, aucun invalide. |
| Combats complets | 12 combats miroir, solo/éleveur/team, builds sauvegardés inchangés : 5 victoires, 5 défaites, 2 nuls contre `3024424` ; aucune erreur ni interruption de tour, des deux côtés. |

Les 12 combats complets sont un test d'intégration et de budget, **pas une preuve de supériorité**. Les cœurs sauvegardés vont de 1 à 22. Les sondes internes utilisent séparément un plafond élevé pour vérifier les calculs ; elles ne servent pas à annoncer des performances de production.

Sur exactement le même état, une évaluation d'entité coûte **345,05 opérations contre 100,05** pour la baseline ; préparer quatre profils coûte **39 869 contre 554**. Il n'y a pas d'appel moteur par valeur d'entité déjà profilée, ni de carte de danger supplémentaire, mais le surcoût est réel. Sur les combats complets, les trajectoires divergent : leurs moyennes d'opérations ne permettent pas d'isoler le coût du seul scoring.

## Ce que les 120 situations établissent, et ce qu'elles n'établissent pas

Il s'agit de **12 familles × 10 configurations** : force, sagesse, agilité, résistance, science, magie, PT, vitalité, boucliers, renvoi, soin continu et placement. Chaque configuration oppose deux tours légaux, avec mêmes builds, carte et graines. Le programme remplit ensuite les PT par des tirs et applique une continuation fixe sur 4 à 6 tours. Les deux choix partent du même état avant action.

Le scoring livré lit l'état natif après chaque alternative, puis sa pénalité terminale. L'oracle indépendant lit la vie finale réelle dans le moteur, y compris les morts avant un appel à l'IA : `PV allié − PV adverse`, avec ±10 000 pour une issue décisive. Il ne lit **aucun coefficient** du scoring. Ce test isole donc le classement des états ; il ne prétend pas que le BFS trouve toutes ces alternatives ni que son simulateur prédit exactement chaque jet.

Un écart est descriptivement « net » lorsque sa moyenne dépasse en valeur absolue une marge t à 95 % + 1 PV. Ce n'est ni un test de promotion ni un contrôle des 120 comparaisons multiples. **89 cas sont nets : 73 accords, 16 désaccords ; 31 autres restent indécis.** Les dix cas de déplacement sont en accord. Les variantes et désaccords sont tous conservés, sans suppression des mauvais résultats.

Lecture des désaccords ([détails chiffrés](../validation/scoring_astra/results/disagreements.json)) :

- **Six cas gagnent 32/32 des deux côtés** : `force-03`, `force-07`, `agilite-07`, `ressources-03`, `periodique-04`, `periodique-08`. L'oracle départage les PV restants, pas la victoire. À 1 000 AGI, dépenser un buff sans bénéfice critique peut retarder des tirs et mieux utiliser le vol de vie. Ce résultat ne justifie pas de créditer de faux critiques au-delà de 1 000.
- **Quatre dépendent de la fenêtre d'observation** : `vie_max-07`, `vie_max-09`, `renvoi-03`, `renvoi-07`. Aucun choix ne perd dans ces échantillons ; l'un termine plus souvent avant la coupure, l'autre conserve parfois davantage de PV. Le bonus terminal de l'oracle accentue cette différence. Le bon horizon stratégique reste à vérifier en combat long.
- **Six appellent une vraie attention tactique** : `resistance-00/05/08`, `science-00/04`, `periodique-02`. La valorisation immédiate et celle des buffs rentabilisés plus tard divergent. Le plus sévère, `science-04`, donne 2 victoires/32 au choix préféré par le score contre 26/32 à l'autre ; `resistance-08` préfère le bouclier immédiat alors que solidification gagne 21/32 contre 0/32. Ce sont des limites connues de cette version, pas des tests déclarés réussis.

## Limites à garder visibles avant de régler à la main

Les coefficients futurs ne font pas une simulation de tous les tours : ils ne planifient pas la disponibilité exacte, la durée de chaque buff, toutes les lignes de vue/AoE, les futurs destinataires ni l'ordre complet des ticks. La pression utilise une répartition moyenne des attaques physiques. Un item mixte soin/dégâts peut additionner des débouchés qui ne se produiront pas sur la même cible. La science des buffs et la magie des entraves utilisent encore des prix génériques. Les interactions simultanées entre attributs sont linéarisées autour des bases, et certains états affectent plus d'opportunités que celles explicitement tarifées ici.

Le simulateur préexistant conserve aussi sa convention de critique déterministe à 950 pour les conséquences immédiates, alors que les coefficients futurs utilisent l'espérance moteur jusqu'à 1 000. Cette différence est déclarée, sans modification du simulateur dans cette branche.

Cette livraison fournit donc un scoring explicable, ses liens au moteur et un banc indépendant avec ses contre-exemples. **Elle ne démontre ni l'optimalité, ni un gain de winrate contre le top.** Pour la suite manuelle, commencer par les six cas tactiques ci-dessus ; éviter d'ajuster un coefficient uniquement pour augmenter le compteur d'accords avec une continuation imposée.

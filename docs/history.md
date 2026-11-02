# Historique des recherches et mesures jusqu’au 31 octobre 2026

Ces notes décrivent leurs versions et campagnes datées. Les règles actives sont dans le [README](../README.md). Le beam complet est conservé sur [variable-beam-search](https://github.com/Traual/Leekwars-AI/tree/variable-beam-search), commit `3cb1123`.


Premier prototype `d756451` : 23 vérifications ciblées réussies, dont une séquence native
Inversion → Armure sur soi → Pistolet. Le cas contrôlé de Solidification à 400 résistance
et 18 PT retrouve le meilleur ordre, sans règle spécifique à cette puce.
Sur 32 états fixes aux budgets souples réels : 13 scores finaux meilleurs, 12 moins bons,
7 sans terminal validé dans les deux versions ; 228 712 511 → 203 121 317 opérations
(−11,19 %, décisions différentes). Aucun dépassement du plafond natif observé.
Le pilote final de 20 combats complets, dix paires miroir 4v4 avec vrais budgets,
donne **5 victoires, 11 nuls et 4 défaites** contre le main `3cb1123`, sans erreur ni
avortement moteur. Cet échantillon ne démontre pas une supériorité de l'évolution.

Suite du 31 octobre : 38 contrôles ciblés passent, dont la confirmation du cast réel,
la revalidation après inversion, la mise à jour des scores/boucliers avec les stats réelles,
le rejet d’une cible morte et la couverture des 16 options dans un test de tirages successifs.
Dans les sondes après cast, avec le RNG corrigé, reprendre la meilleure suite donne 8 scores
meilleurs, 4 identiques et 2 moins bons ; 10 autres états restent sans terminal dans les deux
modes et 8 cas n’avaient pas de première action. Les flux d’actions natifs avant la seconde
décision sont identiques entre les modes. Ce contrôle n’est pas un taux de victoire.

La comparaison suivante utilise de nouvelles graines : **100 combats contre main
`3cb1123` donnent 39 victoires, 22 nuls et 39 défaites**, soit 50 % des points
(intervalle bootstrap par paires : 46,5–53,5 %). Sur 20 combats supplémentaires contre
le premier prototype `d756451` : 5 victoires, 9 nuls, 6 défaites. Aucune erreur ni aucun
avortement moteur dans les 120 combats. La correction du RNG et la reprise sont évaluées
ensemble ici : aucun gain de force n’est démontré et la branche reste expérimentale.

Ce document décrit le code de `main` après la campagne du **22 octobre 2026**, avec les réglages des statiques et le placement Covid du **23 octobre**. Les valeurs indiquées
sont les réglages actuels, pas des paramètres validés pour tous les builds.


## Recherche de suites d'actions

Le **`main` actuel** conserve un beam **indépendant pour chaque première action
positive**, de largeur automatique **1 à 4 par départ**. La largeur est choisie après
la préparation de racine selon le nombre d'actions scorées, les PT restants, leur
coût moyen et les opérations disponibles, puis reste la même à toutes les
profondeurs de la décision. Aucune sélection globale ne supprime des départs.
Chaque parent propose au plus cette largeur de successeurs jouables et positifs ;
seuls les meilleurs états issus de la **même première action** continuent dans son
beam. Le tri compare le score absolu des états, pas seulement le dernier gain.
À largeur 1, chaque départ poursuit une chaîne gloutonne ; à largeur 2, chacun
garde jusqu'à deux suites. Les beams avancent par profondeur avec le budget commun,
qui peut interrompre une vague avant que tous les départs aient été examinés.
La recherche ne développe pas exhaustivement toutes les combinaisons. L'état « ne rien
jouer », toutes les racines positives et tous les successeurs proposés restent des
candidats terminaux, même s'ils ne continuent pas dans le beam.

La **recherche d'actions** regroupe les couples de lancement `From → To` par item,
cibles réellement concernées et distances dans la zone. Chaque groupe conserve ses
origines, puis est scoré après dédoublonnage. Les actions personnelles, les invocations
et les casts dans le vide sont recréés depuis la cellule du nœud : après un tir depuis
X, un auto-buff part de X, même si la recherche avait commencé en Y. Ils n'allouent pas
une map couvrant toutes les cellules accessibles. Les zones visant aussi un allié
gardent leurs origines et leurs différentes distances d'effet ; les lasers utilisent
toute leur ligne, pas seulement leur cellule de visée. Les effets de déplacement
conservent une signature géométrique distincte, car les mêmes cibles peuvent finir
sur des cellules différentes.

Le BFS préfère l'origine actuelle lorsqu'elle existe dans un groupe ; sinon il choisit
une origine accessible avec le même ordre de coût qu'avant, en gardant assez de PT pour
le cast et l'équipement. Les cooldowns, usages et coûts sont relus dans chaque branche.
Le domaine consulté suit la mobilité courante : un gain de PM ou l'accès au Saut
peut créer de nouvelles origines, sans se limiter aux cellules de la racine.

Les gains mémorisés par signature survivent aux changements de `BaseHash` dans une
recherche : seuls les nouveaux groupes sont simulés pour construire leur priorité.
Une zone qui gagne une cible a une nouvelle signature et est encore simulée en entier ;
il n'y a pas de cache additif par cible. Les gains réutilisés sont des **estimations de
classement**, pas des majorants : chaque candidat examiné est simulé et rescorré dans
son parent réel, avec les buffs/passifs du déplacement. Le classement et sa coupure
gloutonne restent approximatifs après une modification de stats. Le debug `[BFS] group
scores: … computed, … reused, … cached orders` rend cette réutilisation visible.

La géométrie est conservée **par plateau et item**, avec les origines déjà calculées.
Un déplacement ou un changement de PM complète uniquement les nouvelles cellules ;
les domaines déjà couverts ne sont pas reconstruits. La disponibilité d'un item reste
vérifiée dans l'état courant, sans figer les cooldowns ou le budget dans ce catalogue.
Une seule visée légale par origine et groupe est conservée ; les zones équivalentes et
les rayons de laser déjà traités évitent aussi leurs vérifications de LoS redondantes.
Les actions ponctuelles et les zones ordinaires sont produites directement, sans
construire une map intermédiaire pour chaque couple origine/visée. Pour une zone,
l'empreinte des autres entités est calculée une fois par centre, puis celle du lanceur
est ajoutée à sa distance propre depuis chaque origine.

Dans les successeurs, le tri réutilise des **clés et des priorités**, jamais des nœuds ni
des actions concrètes d'une ancienne branche. En mode gain brut, le choix d'origine
n'est effectué que pour les nouveaux groupes à scorer et les candidats examinés.
Son cache porte le plateau, la mobilité et les seuils de PT restant **après** le coût du
cast/équipement : un ancien échec faute de PT ne bloque pas une origine devenue payable.
Le mode rendement ajoute aussi les coûts courants à la clé du tri.

Le mélange d'une signature de groupe est calculé une fois dans `SIGNATURE_MIX`,
puis partagé par les empreintes d'ensembles complets ou partiels. Une résurrection
dont la clé inclut le candidat conserve son mélange spécifique.
En gain brut, un ensemble partiel reprend le XOR complet et retire les seules clés
écartées : il n'a plus besoin de reparcourir toutes les clés conservées. Après une
coupure de budget, les nœuds déjà simulés restent disponibles mais le tri devenu
inutilisable n'est pas construit.

En mode gain brut, les priorités inférieures ou égales à epsilon sont exclues du tri,
car la sélection s'arrête déjà à ce seuil. Leurs estimations restent mémorisées et les
nouveaux groupes simulés restent examinés. Dès qu'une estimation change, le cache des
classements est vidé : il conserve uniquement les tris compatibles avec les priorités
courantes, sans accumuler des versions périmées. Les estimations et la géométrie restent
réutilisées.

Chaque ensemble géométrique conserve son dernier classement positif pendant la
recherche. Si les clés correspondent, seules les priorités modifiées sont réinsérées ;
les égalités reprennent l'ordre canonique des clés, comme le tri stable complet.
Un ancien candidat devenu non positif rend la main au tri habituel. L'apparition
d'une priorité positive fait avancer un compteur : les instantanés plus anciens
deviennent inéligibles à la réparation, car ils pouvaient manquer cette clé.
Chaque replan réel libère tous ces classements, en conservant la géométrie. Aucun
nœud ancien n'est repris et aucun gain ancien n'est accepté comme score courant.
Lors de la création d'un ensemble, le dédoublonnage remplit directement `KEYS` et
`FINGERPRINT`, avec les mêmes clés et le même fold que le constructeur de référence.

Chaque estimation porte une entrée `ActionPriorityClass` immuable : les classements
en gain brut partagent directement cette entrée. Un gain modifié la remplace ; les
classements déjà ouverts gardent leur instantané. En mode rendement, une entrée
distincte utilise le coût courant du parent.
Les petits tableaux du beam utilisent l'insertion et la découpe natives ; une
frontière de zéro ou un nœud est réutilisée sans tri. Les appelants la parcourent
sans la modifier et la largeur fixe doit être un entier positif. Lors du jeu réel,
`MoveTo` réussit immédiatement si l'acteur est déjà sur la cellule demandée.
La borne terminale `TERMINAL_BOUND` contient directement le score brut plus la borne
du bonus Covid : elle est remplie lors du scoring du nœud, sans changer `SCORE`.
Les empreintes de mobilité déjà calculées sont relues depuis les couches de Me.
En gain brut, le mémo des résolutions d'origines n'est plus construit : chaque clé
n'y est résolue qu'une fois ; le mode rendement le conserve pour ses lectures du coût.

Les coûts et usages disponibles sont mémorisés sur l'entité simulée. Le mémo est
invalidé au clone et aux écritures de cooldown/usages ; un changement de PT ou d'arme
le remplace également. Le stock brut d'usages jouables est repris depuis le parent
immuable, en retirant les items écrits dans les patches locaux ; coût d'équipement et
financement par les PT restent courants. Chaque phase géométrique retrouve ce mémo
une fois avant sa boucle d'items.

Les générateurs filtrent les items indisponibles ou trop chers avant la géométrie,
et remplissent leurs coûts/usages dans ce mémo. Le BFS consomme ce résultat sans
refaire le même filtrage. Tout nouveau générateur doit respecter ce contrat.

Deux branches sœurs peuvent partager ce mémo seulement depuis le même mémo parent,
avec les mêmes PT, arme et vecteurs locaux de cooldown/usages. Le hash présélectionne,
puis les vecteurs de scalaires sont comparés exactement. Deux stocks indisponibles
ne suffisent jamais à établir cette égalité : leurs cooldowns/usages cachés peuvent
différer. Ce cache est borné à 64 enfants par mémo. Le masque d'items disponibles
n'est publié qu'après lecture complète de l'inventaire ; les scores et origines sont
toujours résolus par la recherche de main. Chaque nœud conserve
aussi les valeurs exactes des entités déjà scorées : ses candidats réutilisent les
valeurs d'avant-cast et calculent celles d'après-cast avec les mêmes formules.

Le mémo `SCORE_MEMO` évite aussi de recalculer la valeur complète d'un clone dont
les inputs de score n'ont pas changé. Une simple dépense de PT/PM ou un changement
d'arme n'invalide pas cette valeur ; les capacités totales, caractéristiques, puissance,
protections, PV, états, durées pondérées, entraves et cooldowns de nettoyage pertinents
sont comparés exactement. Le contexte de scoring doit être le même. Les objets scorés
sont immuables ; les conséquences écrivent leurs clones. Tout nouvel input ajouté au
scoring doit aussi entrer dans `sameEntityScoreInputs`.

À la racine, les actions locales sont scorées avant la génération offensive. Le danger
reste absent de ces simulations et de l'expansion ; il est appliqué au classement final.
Les catalogues d'origines et leurs choix sont vidés
au changement de tour/acteur. `TURN_ACTION_SEARCH_CACHE` conserve les ensembles de groupes
et les estimations entre les casts réels. À chaque nouvelle capture, les modifications
de stats, cellule ou registre d'effets invalident les estimations qui en dépendent ;
un changement du contexte de scoring les vide toutes. Les dépendances utilisent un
masque d'IDs modulo 64 : une collision peut provoquer une invalidation supplémentaire.
Les nœuds et validations de danger ne survivent jamais à une recherche. La racine
resimule tous ses candidats ; dans les branches, les gains conservés restent des
indications gloutonnes, jamais des bornes ni des scores exacts pour un autre état.

La largeur choisie reste la même à chaque profondeur et s'applique séparément à
chaque départ. Il n'y a pas de deuxième boucle sur les estimations nulles/négatives. Les
estimations positives ordonnent les essais, avec une coupure approximative lorsque
assez de successeurs actuels dépassent les estimations restantes pour remplir le beam. Chaque successeur
proposé a été simulé et rescorré dans son parent, déplacements réels inclus. Une
inversion, un kill ou une invocation modifie donc le plateau de sa branche normalement.

Le patch de toute action simulée contient Me. La recherche y lit d'abord ses PT et
sa vie : une feuille morte ou sans assez de PT ne matérialise pas son état complet
pour constater qu'elle ne peut plus être développée.
Les valeurs de scoring et les projections restent celles de main. La validation du
danger se fait seulement après les branches ; aucun candidat brut ne remplace une
évaluation terminale complète. Les réserves de fin de tour et des invocations restent inchangées.

## Budget d'opérations

Le plafond absolu de recherche est fixé par `Play`, à partir du plafond moteur,
des **500 000 opérations de fin de tour** et des **1 000 000 par bulbe** encore à
jouer. La génération utilise cette limite. Pour que le parcours de tous les départs
ne consomme pas le budget du danger, l'expansion protège le coût froid déjà estimé :
sa limite vaut `max(début d'expansion, limite du tour − coût froid estimé)`.
`OPS_DEADLINE` est restaurée avant le classement terminal. Les phases utilisent la
même garde `OPS_SEARCH_MARGIN = 250000`, entre les éléments de leurs boucles.
L'entrée de chaque décision utilise aussi cette
marge et reprend la vidange existante quand la recherche doit s'arrêter.
Une préparation interrompue n'est jamais publiée
dans un cache qui promet un résultat complet.

La **largeur** remplace le quota strict de 20 % et le minimum de 250 000. Après les
racines, on calcule le coût moyen **en PT** des actions positives, déplacement et
équipement compris. La règle est :

```text
longueur estimée = max(1, PT restants / max(1, coût moyen))
disponible estimé = max(0, limite du tour − opérations utilisées − coût froid estimé)
cible d'expansion = disponible estimé × 0,5
unité de coût = cible d'expansion / (max(16, actions initiales) × longueur estimée)
```

Les PT n'interviennent qu'une fois dans cette normalisation. Diviser aussi la cible
par la longueur pénalisait le début du tour au carré et faisait monter le beam
trop tard. Le facteur `0,5` laisse une estimation pour les recherches suivantes ;
il dimensionne la largeur, sans ajouter de quota ni de coupure intermédiaire.

| Unité de coût, en opérations | Largeur |
| --- | ---: |
| ≥ 40 200 | 4 par départ |
| ≥ 32 100 | 3 par départ |
| ≥ 22 900 | 2 par départ |
| En dessous | 1 |

Ces seuils se règlent dans `beamWidthFor`. Le beam choisi reste constant pendant
la décision ; `BFS_BEAM_WIDTH > 0` impose encore une largeur manuelle. **La cible
n'est pas une limite** : une recherche peut la dépasser et finir sa profondeur.
La décision suivante repart des PT et opérations réellement restants. Si le coût
froid absorbe le disponible, les racines déjà scorées restent candidates et la
priorité va à leur validation avec danger ; l'expansion peut s'arrêter avant de
prolonger les départs. La largeur 1 n'est donc pas une garantie de parcours complet.
Les coûts restent des estimations, pas des bornes universelles.

Le danger est calculé **uniquement au classement terminal**. Sa limite utilise la
partie autorisée de la réserve de fin de tour ; les 300 000 opérations du placement
réel restent protégées. Si les structures sont froides, `prepareGeometry` construit
leurs géométries **item par item**, avec un contrôle **entre les origines de lancement**
dans les items coûteux. C'est la même garde et les mêmes caches existants.
Chaque item et chaque couverture publiés sont complets. Une interruption conserve
les items déjà construits et les LoS complètes, sans publier une couverture tronquée. Les constructeurs
et les formules de danger/soin restent inchangés. Au large, une clôture dont le coût
froid estimé tient entièrement peut emprunter directement le chemin habituel.

La réserve globale de 500 000 inclut la part stricte de placement de 300 000 : elles
ne s'additionnent pas. La vidange garde son contrôle du coût d'exécution observé ;
le placement réel garde ses 25 000 pour le dernier déplacement. Le plafond propre
à la préparation d'`EnemyOffense` reste inchangé. Ces protections d'exécution ne
sont pas des quotas supplémentaires dans l'expansion du beam.

Après un cast réel, les nœuds sont abandonnés ; la capture suivante réutilise les
catalogues et les estimations encore valides, puis resimule les candidats examinés.
Si aucune clôture complète avec danger ne tient, on finit les actions et passe au
placement, au lieu d'exécuter un plan brut. Les replis de placement sans danger
restent annoncés. Une garde entre blocs ne garantit pas le coût de tout bloc
indivisible ; les interruptions et plafonds natifs restent mesurés en réception.

La couverture offensive de danger assemble maintenant les items **à la demande pour
chaque cellule consultée**, en partageant leurs géométries. Le placement de Me ne
construit que la carte de soin de son camp, dans un cache séparé ; les projections
d'autres cibles gardent l'API complète. Ce sont des économies de calcul, sans nouvelle
approximation de danger ou de soin. Les replis sans danger restent possibles si aucun
plan validé n'a pu être financé, et gardent leurs avertissements explicites.


### Mesure des caches du 15 octobre 2026

Lot fixe de dix combats d'éleveur par variante, adversaire `6c145af`, mêmes graines,
builds et cœurs réels. Les deux caches conservés produisent, sur les 44 fenêtres de
tour communes à tous les essais et plafonnées dans le témoin, **19,5 % de steps et
20,6 % de tentatives de développement supplémentaires**, pour **7,8 % d'opérations
en moins**. La profondeur maximale moyenne ne progresse pas (5,75 → 5,41).
Sur ce lot, témoin et finale font chacun **4 victoires, 2 nuls, 4 défaites**, sans
erreur ni tour avorté : ce test établit un gain d'efficacité observé, pas un gain de
winrate. Les compteurs sont propres aux bundles de mesure et ne sont pas livrés.
L'essai de reprise du plan précédent a été retiré, faute de bénéfice mesuré.

Un second essai conserve le suffixe seulement comme candidat terminal, sans place
réservée dans l'expansion. Sur les 65 tours identiques avant sa première divergence,
il augmente le coût de `Play` de **0,37 %**, et économise seulement 2 064 opérations
de calcul du net pour 2,73 millions dépensés à reprendre les plans : il reste retiré.

Un contrôle 4v4 séparé (graine **1756442328**) relève temporairement les plafonds
natifs et internes, sans changer le générateur ni les builds. Il joue 64 rounds
sans erreur ni coupure. Les raccourcis exacts évitent un tri intermédiaire, une
seconde consultation du placement d'un même plan validé, le parcours des clés
déjà toutes connues dans une recherche et les gains positifs lors du second passage
réservé aux actions dont le gain estimé est nul ou négatif. Sur ce contrôle, le déroulement complet
reste identique : **12 076 actions, 20 789 steps et 29 476 développements** pour
**4 118 304 opérations de moins (0,41 %)** dans les bundles instrumentés.
Les limites normales restent actives dans l'IA livrée ; ce petit gain ne démontre
pas un gain de winrate. Les origines et scores des candidats examinés restent
revalidés ; les clés connues sont remises à zéro à chaque nouvelle recherche.

La passe suivante conserve les formules et coefficients, et cible des postes plus
importants : mémo séparé du potentiel des caractéristiques, calcul paresseux des
protections/soins dont le stock est nul, mélanges de hash du profil offensif
mémorisés pendant le tour, et courbes de portée entièrement dominées ignorées.
Les deux orientations du même contrôle 4v4 restent identiques octet pour octet
(hors opérations/logs), avec les mêmes simulations, steps et développements :

| Camp mesuré | Opérations avant | Après | Économie |
| --- | ---: | ---: | ---: |
| `4-left` | 1 004 728 333 | 940 763 437 | **6,37 %** |
| `4-right` | 6 152 826 540 | 5 950 071 846 | **3,30 %** |

Les durées de buffs, six entraves, Manumission, capacités PT/PM et états sont
vérifiés par le mémo des caractéristiques ; les PV l'invalident aussi pour un
porteur de Châtiment. Une nouvelle dépendance introduite dans un coefficient doit
être ajoutée aux validations de mémos correspondantes. La sonde compare **360
mutations** au calcul direct sans ce mémo, sans écart. Le test final aux limites
normales ne présente aucune erreur ni tour avorté ; il ne mesure pas la winrate.

## Diagnostics

| Sortie ou réglage | Signification |
| --- | --- |
| `[COEFS]` | Valeurs des coefficients, importance de base, potentiel courant, facteurs PT/PM et contributions périodiques. |
| `ScoringDebug.ENABLED` | Actuellement `false` : coefficients non affichés. `true` active l'affichage au début du tour, sans pause. |
| `Best: … bases, … steps` | Première action du plan retenu et delta terminal. Les bases sont les entrées de racine ; les steps sont les successeurs ajoutés aux suites, pas toutes les simulations effectuées. |
| `[BFS] group scores: …` | Scores calculés, estimations reprises, tris servis et estimations conservées depuis les casts précédents. |
| `[BFS] expansion target: … beam width per start: … starts: …` | Cible indicative, largeur indépendante par première action et nombre de départs positifs conservés. |
| `[BFS] expansion completed …` / `expansion truncated: operation budget …` | Bilan systématique de l'expansion : `depth` est la profondeur maximale réellement atteinte (nombre d'actions), `beam width` la largeur conservée par départ. `beams processed` compte les beams entièrement traités dans la dernière vague parmi ses beams actifs ; `parents processed` précise l'avancement du dernier beam, y compris une coupure dans un successeur. `wave depth` est la profondeur que cette vague tentait de produire ; elle peut dépasser `depth` si aucun successeur n'existe. `starts` est le nombre initial de départs positifs, `steps` le total des successeurs ajoutés et `operations` le coût de l'expansion depuis la fin de la préparation racine. `completed` signifie terminé selon les règles du beam, pas exploration exhaustive de tous les combos. |
| `[BFS] expansion skipped: …` | Aucune expansion lancée, faute de PT ou de score initial complet ; profondeur, largeur et compteurs à zéro. |
| `[BFS] danger not taken into account: no affordable terminal plan, stop actions` | Aucun candidat complètement évalué avec danger ; arrêt des actions, sans repli brut. |
| `[VIDANGE] danger not taken into account: raw-score plan` | La suite exécutée sans nouvelle recherche venait d'un plan non validé avec danger. |
| `[placement] danger not taken into account: spatial cost only` | Le placement réel n'a pas pu financer le danger ; seuls les coûts spatiaux sont pris en compte. |
| `[placement] offensive potential truncated: preparation budget` | Préparation du profil offensif limitée localement à 200 000 opérations (ou à la réserve de fin de tour). Affiche coût, casts évalués et budget de planification restant. Ce message n'arrête pas le BFS et ne prouve pas que le tour manque d'opérations. |
| `[BFS] stop actions: planning budget exhausted` | Le budget de planification du tour est épuisé ; tentative de vidange du dernier plan avant placement. |
| `[BFS] stop actions: initial scoring exceeded planning budget` | Le score initial n'a pas pu être terminé avant le plafond de planification. |
| `[BFS] no action selected: idle plan wins terminal scoring` | Le candidat sans action gagne le classement terminal ; ce n'est pas une erreur d'exécution. |
| `[placement] covid score: … danger cost: … spatial cost: …` | Contributions en unités de score pour le placement final réellement choisi ; désactivable avec `CovidPlacement.DEBUG = false`. |
| `[FAIL]`, `[FAIL x2]` | Échec réel et replanification, puis arrêt sur le deuxième échec identique. |
| `BenchOps.ENABLED` | Actuellement `false`. Le passer temporairement à `true` pour profiler les opérations ; les catégories imbriquées se recouvrent et ne doivent pas être additionnées comme des coûts exclusifs. |

Les debug servent à comprendre les choix. Le journal d'actions du moteur reste la
source de vérité pour ce qui a réellement été joué : `Using :` ne couvre pas les
casts de déplacement et les chemins spéciaux d'invocation/résurrection.


## Campagne de recherche du 22 octobre 2026

**Le beam actuel est conservé.** Les essais ne justifient pas de promouvoir la
recherche à deltas exacts ou d'empiler de nouveaux réglages. Les corrections communes
des caches d'ordre/séquence sont livrées ; coefficients, conséquences, projections,
réserves et largeur du beam restent inchangés.

1 800 combats de force à budgets natifs, en neuf comparaisons de 200, avec 100 blocs
miroir par duel. Le moteur, les builds, les sources et les scénarios sont figés.
Les cinq premières variantes ne changent qu'un composant de la même base exacte
corrigée, sur les mêmes 100 scénarios ; elles ne sont pas directement comparées au beam.

| Variante / base exacte | Points de la variante | p apparié ajusté Holm |
| --- | ---: | ---: |
| Budget de décision 35 % | 53 % | 0,208 |
| Budget de décision 50 % | 48 % | 0,612 |
| Sans validations intermédiaires | 48 % | 0,612 |
| Deux successeurs, autres politiques conservées | 50,25 % | 1 |
| Sans bifurcations économiques | 48,25 % | 0,612 |

Le budget 35 % consomme environ 19,5 % d'opérations totales de plus et subit une
interruption native contre zéro dans son témoin. Le beam exact à deux successeurs
consomme environ 6,9 % de plus pour un score presque égal. Ces coûts sont descriptifs
sur des trajectoires différentes, pas des gains/pertes byte-exacts. Aucun composant
ne satisfait la règle de sélection et de sécurité préinscrite.

La base exacte est ensuite reçue sur des graines nouvelles : **46 % contre le beam
corrigé**, puis **50 % contre le main historique 1cbeb61 corrigé**. Le test apparié
contre le beam donne p brut 0,02495, soit 0,04991 après correction des deux réceptions.
L'ancien classement ne suffit donc pas pour la promouvoir.

Un duel frais départage directement les références corrigées :

| Beam corrigé / adversaire | V / N / D du beam | Points du beam |
| --- | ---: | ---: |
| Exact de base | 82 / 52 / 66 | 54 % |
| Historique 1cbeb61 | 83 / 47 / 70 | 53,25 % |

Le deuxième duel a p=0,059 : il ne prouve pas une supériorité universelle ; il ne
justifie pas non plus de remplacer l'incumbent. **Aucun diagnostic ni tour interrompu
n'est observé pour le beam sur ces 400 combats.** Un nul vaut 0,5 point : ces scores
ne sont pas des pourcentages de victoires.

Enfin, un seul essai ciblé sépare la fenêtre de danger terminale du budget d'expansion
de l'exact : 51,75 % contre sa base, p=0,213, et deux interruptions natives contre une.
Il est écarté sans nouveau réglage ni confirmation inutile. Toutes les interruptions
restent comptées. Le premier lot incomplet, arrêté pour corriger les ordres fractionnaires
avant lecture de son classement, est archivé séparément et exclu des 1 800 résultats.

Les clés communes ont changé depuis le tournoi précédent : ses résultats ne sont
pas agrégés à cette campagne. Les intervalles et tests portent sur les blocs miroir,
pas sur 200 observations supposées indépendantes. Les résultats restent propres au
snapshot local du méta et ne garantissent pas tous les builds adverses.

Les sources, manifestes, sorties brutes, contrôles natifs, profils et rapport détaillé
sont conservés localement dans `work/search-ablation-20261022/`. Les sondes ne sont
pas livrées dans l'IA. Contrôles complémentaires : 88 règles, 105 invariants historiques,
19 invariants du beam exact et plus de 60 000 gains réutilisés vérifiés sans écart
supérieur à 1e−6. Le scoring et les budgets de production ne sont pas réglés sur les scores.

## Mesures historiques

Les résultats suivants décrivent des versions et lots précis ; ils ne sont pas
un test de réception permanent de la tête actuelle. Le comparatif de l'ancien
arbre inclut le changement de quota de décision : il n'isole pas le gain des caches.

Comparatif du 12 octobre 2026 contre l'ancien arbre `adbf60d`, à scoring identique,
avec les cœurs réels et les deux orientations par bloc :

| Lot de confirmation | Combats | Victoires | Nuls | Défaites | Score |
| --- | ---: | ---: | ---: | ---: | ---: |
| Compositions identiques | 64 | 19 | 36 | 9 | 57,8 % |
| Compositions différentes | 64 | 31 | 10 | 23 | 56,3 % |
| Ensemble | 128 | 50 | 46 | 32 | 57,0 % |

Le score compte un nul pour une demi-victoire. Aucun tour avorté ni erreur moteur
n'a été observé pour cette version, invocations comprises. Ces lots ne prouvent pas
une domination sur tous les builds ou formats. Les budgets de 20 % et 35 % donnent
le même score moyen dans les deux comparatifs communs de 32 combats chacun ; le
réglage court conserve davantage d'opérations pour les décisions suivantes.

Une seconde passe de caches réutilise les ensembles de groupes dans une recherche,
les quatre couches de déplacement de Me et la carte des occupants. Les actions
ponctuelles sans ligne de vue partagent leur domaine complet de portée ; la résolution
filtre toujours les origines par les déplacements réellement disponibles. Les valeurs
d'entités intactes sont reprises dans les ancêtres déjà scorés. Le placement calcule
son préfixe spatial et l'importance une fois par balayage, puis réutilise les expositions
identiques. Ces caches conservent les formules et la revalidation des actions.

Sur une sonde fixe, cette passe réduit de 8,4 % le coût de génération des actions et
de 6,3 % celui de préparation des successeurs au budget de 12 millions d'opérations.
Elle visite 8 plutôt que 7 steps au petit budget, et 52 dans les deux versions au grand
budget. Ce sont des mesures locales, sans garantie de davantage de steps sur tout combat.
Le comparatif de 128 combats ci-dessus précède cette seconde passe.

## Portages de performance sur main — 18 octobre 2026

La recherche de main et sa deuxième boucle de réexamen des gains nuls/négatifs étaient
conservées. Aucun cache de gains exacts de `search-exact-deltas` n'est importé.
Les portages concernent la disponibilité d'inventaire, la génération d'actions et
la résolution des origines : stock hérité, partage entre branches sœurs sous preuve
exacte, masque de disponibilité complet, accès locaux, dédoublonnage par map native,
cache pur des contributions géométriques et arrêt de marche au rang 1. La cellule
courante étant déjà exclue, aucune origine restante ne peut battre ce rang ; les
égalités gardent la première origine. L'empreinte propre aux groupes de main reste
calculée par son constructeur.

Sur dix fenêtres fixes de 4v4, tronquées à une ronde et sans plafond dans la copie
de mesure, les **mêmes journaux, 43 071 steps, 57 635 développements et 323 décisions**
passent de **1 243 320 329 à 1 137 336 222 opérations natives : −8,52 %** par rapport
à `13e20a4`. Aucun changement des formules, budgets de production, builds ou générateur.
Cela mesure le coût à travail identique, pas une amélioration de winrate.

Réception : **18 contrôles ciblés** d'inventaire ; **163 424 coûts/usages** et
**39 079 résolutions d'origine** comparés aux lecteurs directs et à l'ancien balayage,
sans écart. Sur deux témoins de quatre rondes aux budgets réels : **5 536 → 6 793 steps**,
avec des trajectoires différentes, sans erreur ni tour avorté. Cette dernière mesure
n'est donc pas une comparaison de coût à travail identique ni un duel de force.

La suppression de la deuxième boucle et la mise en cache d'objets PlayableAction ont
été essayées séparément. La première change la politique de recherche ; les deux
petits témoins ne suffisent pas à justifier son adoption. La seconde n'économise que
0,033 % supplémentaires sur ces témoins : elle n'est pas conservée.

## Essai du beam search — 19 octobre 2026

Le candidat `search-beam` **41074cd** (largeur 3, sans deuxième boucle) a joué
**100 combats 4v4 complets** contre main **1cbeb61**, qui garde sa deuxième boucle.
Les 50 scénarios sont rejoués dans les deux orientations : mêmes cartes et builds,
budgets réels, limite de 64 tours, sans instrumentation des IA. La graine de campagne
est `2026101901`. Les sources sont figées avant le premier combat, sans ajustement
ni arrêt sur les résultats.

| Résultat du beam | Valeur |
| --- | ---: |
| Victoires / nuls / défaites | 40 / 24 / 36 |
| Points, un nul valant 0,5 | 52 / 100 |
| Intervalle à 95 % des points, bootstrap des 50 paires | 46,5–57,5 % |
| Paires favorables / équilibrées / défavorables | 8 / 36 / 6 |
| Erreurs LeekScript / coupures moteur, deux camps réunis | 0 / 0 |

Les neuf contrôles ciblés vérifient notamment la ramification à deux profondeurs
et les scores contre un recalcul complet des états ; deux courts témoins aux budgets
réels précèdent le gel. Le beam fonctionne, mais ces 100 combats ne démontrent pas
sa supériorité sur main. Les trajectoires diffèrent : les totaux d'opérations et de
steps de ce duel ne mesurent pas une économie à déroulement identique. Main reste
inchangée et cette branche demeure expérimentale.

## Historique : première calibration adaptative du 19 octobre 2026

Les réglages de cette section correspondent au commit **797ae24**, avant la garde
commune et la prise en compte des PT décrites dans la section budget actuelle.

Les 100 combats ci-dessus concernaient la largeur fixe 3, avant cette évolution.
`beamWidthFor` utilise le budget d'expansion divisé par `max(16, nombre d'actions)`.
Les paliers se règlent directement dans `User/BFS.leek` :

| Opérations disponibles par action | Largeur |
| --- | ---: |
| ≥ 43 200 | 12 |
| ≥ 30 300 | 8 |
| ≥ 20 400 | 6 |
| ≥ 10 300 | 4 |
| ≥ 6 500 | 3 |
| ≥ 3 500 | 2 |
| En dessous | 1 |

La calibration compare 7 largeurs depuis **12 mêmes états** de combat : 84 mesures,
avec les mêmes inputs, le même contrôleur avant la décision et le vrai budget de danger.
Seule l'expansion diagnostique est libérée pour en mesurer le coût complet. Les seuils
prennent le quantile 90 % du coût par action, augmenté de 20 %, puis arrondi par 100.
Un corpus distinct prévoit 12 points de contrôle ; 11 sont atteints dans les deux
variantes. Les **11 expansions adaptatives terminent dans leur budget**, avec une
largeur moyenne de **8,64**, contre 3 pour le témoin. Ce modèle empirique n'est pas
une borne sur tous les combats : invocations, nouvelle géométrie et clôtures froides
peuvent encore provoquer un surcoût.

La réception comporte **20 combats courts 4v4**, dix paires miroir de huit rounds,
avec les budgets natifs et les builds sauvegardés. Aucun changement de cœurs ni de
réserve n'est livré. Les compteurs légers donnent :

| Mesure, décisions de poireaux avec actions de racine | Adaptatif | Fixe 3 |
| --- | ---: | ---: |
| Décisions observées | 2 593 | 2 537 |
| Largeur moyenne choisie | 7,05 | 3 |
| Expansions sans troncature | 2 549 (98,3 %) | 2 467 (97,2 %) |
| Successeurs retenus dans les recherches | 122 414 | 33 351 |
| Opérations d'expansion cumulées | 615 717 620 | 307 254 020 |
| Erreurs LeekScript / coupures moteur, deux camps réunis | 0 / 0 | 0 / 0 |

L'adaptatif dépense davantage pour élargir les tours qui le permettent et se réduit
sur les décisions chargées. Les trajectoires diffèrent : ces totaux décrivent la
réception, sans prouver un gain de force contre main ni une économie à jeu identique.

Les simplifications exactes retenues fusionnent les parcours de racine, écartent
directement les candidats sous le dernier successeur retenu et lisent les coûts
déjà mémorisés avant d'appeler leur calcul. Sur 12 contrôles, les traces de toutes
les actions simulées, origines et scores restent identiques : 47 289 612 → 47 267 959
opérations de décision, soit **0,046 %** seulement (0,20 % sur l'expansion).
Le remplacement du tri natif et un cache supplémentaire de priorités ont été écartés :
gain trop faible pour le premier, léger surcoût pour le second.

Les gardes d'opérations sont conservées. Un micro-benchmark de 100 000 itérations
mesure environ **3 opérations par garde complète**. Même en comptant toutes les
lectures du compteur du BFS et de la génération comme supprimables, le plafond
observé n'est que **0,18 %** du coût des décisions de calibration en largeur 3.
Les troncatures restantes et les coûts indivisibles ne permettent pas de remplacer
ces protections par le seul choix de largeur. Scoring, simulation, placement et
budgets de production restent inchangés.

## Historique : garde commune et beam global selon les PT — 19 octobre 2026

Cette évolution remplace les anciens paliers ci-dessus ; son fonctionnement et les
réglages actuels sont dans la section budget. Calibration : **24 états** (8 points
de combat × 3 niveaux de PT) × 7 largeurs, soit 168 expansions utiles. Même état initial
entre largeurs ; aucune modification de build. Les paliers utilisent q90 +20% du
coût normalisé par actions et longueur estimée. Un corpus distinct de 24 états
(48 essais auto/fixe3) termine toutes ses expansions automatiques sous les vrais
plafonds ; largeur moyenne7.96. Les3 cibles nulles font encore
une petite recherche de largeur 1. Les seuils ne sont pas réajustés sur ce corpus.

Quatre états à largeur fixe 3 donnent 375 simulations exactement identiques :
items, origines, cibles, coûts, scores, ordre et plateau. Coût de décision agrégé :
**16,320,282→16,044,214 opérations, soit−1.69%**.
Ce petit échantillon ne mesure pas tous les tours ; deux états ont un léger surcoût.

Réception finale :20 combats courts 4v4,10 paires miroir de 8 rounds, contre le beam
précédent 797ae24, avec les budgets natifs et les builds sauvegardés :

|Mesure|Nouveau|Précédent|
|---|---:|---:|
|Décisions avec actions de racine|2303|2569|
|Largeur moyenne|8.49|7.27|
|Profondeur maximale moyenne|2.963|2.821|
|Profondeur du plan choisi|2.182|2.299|
|Steps retenus|148545|125575|
|Opérations de décision cumulées|2863688756|2504088723|
|Erreurs LeekScript/coupures moteur,deux camps|0/0|0/0|

Les trajectoires diffèrent :+18.3% de steps et+14.4% d'opérations sont des
observations de réception, pas une économie à combat identique ni une preuve de
force contre main. Le danger n'est calculé qu'après l'arbre. La réception compte
encore25 arrêts d'actions faute de clôture complète et27 placements sans danger
dans le camp nouveau : les replis sont explicites. Main reste inchangée.

Une coupure du premier prototype à 4 cœurs a révélé un item géométrique de845563
opérations. Le correctif interrompt sa préparation **entre origines**, en conservant
seulement des géométries complètes et les LoS déjà calculées. Le cas régressif, les
48 essais indépendants et les 20 combats natifs passent ensuite. Dix-huit contrôles
fonctionnels passent, dont l'égalité des géométries et le choix terminal contre un
oracle exhaustif. Scoring, simulation, projections et réserves restent inchangés.

## Historique : répartition du beam global dans le tour — 19 octobre 2026

La double division par `PT / coût moyen` est remplacée par une seule, avec une cible
indicative de 50 % du disponible estimé. Les seuils de largeur, les réserves et les
gardes ne changent pas. Les formules de scoring et le classement terminal avec
danger restent identiques.

Six scénarios distincts, rejoués avant/après pendant huit rounds avec les budgets
natifs, donnent les mesures suivantes. Trois ont servi au diagnostic, trois autres
au contrôle du réglage retenu :

| Mesure | Avant | Après |
| --- | ---: | ---: |
| Décisions avec candidats | 1 660 | 1 663 |
| Premier beam moyen, tours d'au moins trois décisions | 5,05 | 6,90 |
| Dernier beam moyen, mêmes critères | 9,61 | 10,24 |
| Steps retenus | 99 174 | 133 689 |
| Coupures d'expansion par budget | 24 | 20 |
| Coupures pendant les racines | 28 | 24 |
| Opérations des décisions | 1,97 milliard | 2,24 milliards |
| Arrêts sans plan terminal finançable, camp suivi | 5 | 6 |

Les steps augmentent de 34,8 % pour 13,7 % d'opérations de décision en plus. Les
trajectoires diffèrent : ce sont des observations de répartition, pas une économie
à déroulement égal ni une preuve de meilleure winrate. Sur les trois contrôles pris
seuls, les coupures d'expansion passent de 10 à 11 ; leur total sur les six baisse.
Les fins de tour peuvent encore afficher 12 : peu d'actions et peu de profondeur
les rendent peu coûteuses. L'estimation du coût froid peut encore imposer un
premier beam étroit ; elle est conservée.

Dix-huit contrôles passent, dont les scores comparés à l'oracle complet aux
profondeurs 1 et 2 et le danger terminal. Trois combats courts supplémentaires sans
instrumentation ne présentent aucune erreur ni interruption moteur. Les mesures
restent hors de l'IA de production.

## Beam indépendant par première action — 19 octobre 2026

Le beam global est remplacé par un beam pour chaque départ positif. Les états
d'un départ ne peuvent plus éliminer ceux d'un autre. La largeur automatique va
de 1 à 4 ; les constantes se règlent dans `beamWidthFor`. Les autres méthodes de
simulation, de scoring, de sélection d'origine et de classement terminal sont
conservées.

La calibration compare **18 mêmes états × 4 largeurs = 72 expansions complètes**,
avec PT virtuels plafonnés à 8, 16 ou non plafonnés dans les seules sondes. Les seuils
prennent le quantile 90 % du coût normalisé, augmenté de 20 %. Un contrôle sur
12 autres états, auto contre fixe1 avec les limites natives, donne 10 expansions
automatiques complètes et 9 sous la cible indicative, sans erreur moteur. Les
coupures sont conservées dans le résultat ; les seuils ne sont pas réajustés dessus.

Un premier prototype consommait trop de budget avant le danger, même en largeur 1.
Le coût froid déjà estimé est donc protégé pendant l'expansion, puis la limite de
tour est restaurée avant le classement terminal. Cela n'ajoute ni quota de 20 % ni
validation anticipée du danger. Les constantes de réserve restent identiques.

Sur trois scénarios fixes en self-play pendant huit rounds :

| Mesure | Beam global | Par départ, fixe1 | Par départ, automatique |
| --- | ---: | ---: | ---: |
| Départs examinés pour prolongement avec ≥20 TP | 40,4 % | 82,8 % | 83,5 % |
| Largeur moyenne | 9,64 globale | 1 par départ | 2,22 par départ |
| Steps retenus | 73 098 | 45 115 | 67 239 |
| Parents examinés | 20 960 | 58 717 | 67 985 |
| Opérations des décisions | 1,16 milliard | 1,49 milliard | 1,52 milliard |
| Arrêts sans plan terminal finançable | 2 | 2 | 0 |

Face au fixe1, l'automatique retient 49,0 % de steps en plus pour 1,9 % d'opérations
de décision en plus sur ces trajectoires. Les choix et combats diffèrent : ces
mesures ne prouvent ni une économie à déroulement égal ni une meilleure winrate.

**26 contrôles natifs passent**, dont 88 départs tous conservés aux largeurs 1 et 2,
séparation des beams à chaque profondeur et scores contre l'oracle complet
(écart maximal 3,64e−12). Trois autres combats sans instrumentation ne présentent
aucune erreur ni interruption moteur ; ils comptent encore 5 arrêts sans plan
terminal et 7 placements spatiaux seuls, contre 4 et 5 dans la réception figée du
beam global sur ces mêmes scénarios. Ces limites restent explicites. Aucune
instrumentation de mesure n'entre dans l'IA de production.

### Comparaison sur 200 combats du 20 octobre

Le snapshot du beam par première action, avec les adaptations de règles mais
**avant ces deux corrections finales de cache**, a affronté l'ancien main
`1cbeb61` sur 100 paires miroir nouvelles, aux budgets natifs : **60 victoires,
62 nuls, 78 défaites**, soit **45,5 % des points**. L'intervalle bootstrap à 95 %
par paire est **[41 % ; 50 %]** : aucune supériorité du candidat n'est démontrée.
Les erreurs restent comptées : huit diagnostics de cache dans le candidat,
un dépassement d'opérations dans l'ancien main. Aucune interruption native n'est
émise côté candidat ; ces huit diagnostics empêchent néanmoins de présenter
ce duel comme une validation sans erreur.

Les sources, scénarios, builds et résultats bruts du duel sont figés séparément.
Les corrections finales sont validées par des cas de régression et des combats
de contrôle ; leur taux de victoire n'est pas mesuré par cette série de 200.

### Optimisations exactes du 29 octobre 2026

Après `5544fdc`, les signatures mélangées et les entrées de priorité immuables sont
réutilisées, les contrôles de disponibilité redondants sont retirés et les feuilles
sans suite sont reconnues avant matérialisation. Les règles de sélection, formules
de scoring et budgets de production restent inchangés.

Sur quatre témoins 4v4 de deux rounds, à largeurs fixes 1 et 2 et sans coupure de
recherche dans le seul banc : **64 tours de poireaux**, **1 424 840 374 → 1 360 681 950
opérations**, soit **−4,50 %**, avec les actions natives strictement identiques.
Les 128 recherches isolées à différents PT et sur quatre plateaux conservent aussi
leurs simulations, scores et suites. Les **325 contrôles ciblés** passent, ainsi
qu'une recherche en mode rendement avec résurrection. Deux combats courts aux
budgets réels terminent sans erreur ni interruption native. Le profil final confirme
la baisse du travail de préparation des groupes ; ces mesures de coût ne mesurent
pas un gain de taux de victoire.

Une deuxième passe, depuis `680069c`, simplifie les empreintes partielles, évite
un tri après coupure, utilise les primitives natives pour les petits beams et
court-circuite un déplacement vers la cellule actuelle. Sur les mêmes **64 tours**,
**1 360 681 950 → 1 354 750 082 opérations**, soit **−0,44 % supplémentaires**, avec
les actions natives identiques. Les 128 recherches isolées conservent **175 845
simulations** identiques ; le contrôle rendement/résurrection en ajoute 895, dont
19 résurrections. Les 325 contrôles passent, ainsi que deux combats courts aux
budgets réels sans erreur ni interruption. Les témoins de référence sont réutilisés
après vérification des sources et d'un coût de recherche identique.

Un essai séparé réserve les places du beam aux états encore développables, les
fins de suites restant candidates à la validation terminale. Aux budgets réels,
sur 75 états comparables, il améliore dix scores et en conserve 65 ; 21 autres
états n'ont de terminal validé dans aucune des deux versions. Sans coupure, deux
scores régressent sur 96 états. Les dix combats miroir donnent **4 victoires,
2 nuls, 4 défaites**, avec un dépassement d'opérations côté référence. Cet essai
reste hors du code publié : ces mesures ne démontrent aucune supériorité.

### Optimisations du 30 octobre 2026

Depuis `27be191`, le BFS répare son dernier classement compatible par réinsertion,
réutilise l'empreinte des couches et évite les mémos d'origines à usage unique. La
génération calcule une signature par cible ponctuelle, sort les attributs immuables
des boucles et ne compte les invocations que lorsqu'un cast disponible le demande.
La borne terminale est précomposée. Les formules, simulations d'effets, gardes d'opérations,
réserves, largeur du beam et politique de sélection restent inchangées.

Les **112 tours de poireaux** sur quatre plateaux, à largeurs fixes et sans coupure
dans le banc, passent de **2 589 360 464 à 2 542 108 157 opérations (−1,82 %)**,
avec les actions natives strictement identiques. Les **128 recherches isolées**
gardent leurs **175 845 simulations** et suites ; le contrôle rendement/résurrection
en ajoute 895, dont 19 résurrections. Les **325 contrôles habituels** et **512 contrôles
du classement** passent. La géométrie de **148 objets** et les extensions successives
de leur catalogue restent identiques. Deux combats courts aux budgets natifs réels
terminent sans erreur ni interruption.

Sur 96 états fixes avec les budgets souples réels et largeurs 1/2/4, 75 ont un terminal
comparable : **5 scores améliorés, 70 identiques, aucun moins bon**. Les 21 autres
n'ont de terminal validé dans aucune version. Le banc observe **31 609 → 32 081 steps**
et **46 019 → 46 516 simulations**, pour **−0,81 % d'opérations**. Le plafond natif est
désactivé uniquement pour cette observation ; aucune mesure ne le dépasse. Ce contrôle
de décisions ne mesure pas un taux de victoire et ne garantit pas ces gains sur tous
les combats. Les témoins de référence réutilisés sont vérifiés contre les sources figées.

La passe suivante, depuis `f2d52dc`, déplace les instantanés de tri dans leurs ensembles,
les libère aux replans, traite les égalités selon les clés et fusionne les parcours de
construction d'index. Sur les mêmes **112 tours**, **2 542 108 157 → 2 469 170 665
opérations (−2,87 % supplémentaires)**, avec les actions natives identiques. Les
128 recherches isolées conservent leurs 175 845 simulations ; le contrôle de rendement
avec résurrection en ajoute 895. Les 325 contrôles habituels et les propriétés de
classement avec clés signées de 64 bits passent. **6 077 ensembles** construits en un
passage retrouvent les clés et l'empreinte du constructeur de référence. Deux combats
courts aux plafonds natifs réels terminent sans erreur ni interruption.

Sous budgets souples réels : **3 scores améliorés, 72 identiques**, et 21 états sans
terminal validé dans les deux versions ; **32 081 → 32 905 steps**, pour **−0,58 %
d'opérations**. Les mesures de mémoire sur 96 tours à largeur 2 restent sous 13 %
du plafond moteur observé ; aucun instantané d'un ancien replan n'est conservé.
Les variantes de LoS, de borne de distance et de file de priorité ont été écartées,
ainsi qu'un index supplémentaire des égalités. Les formules, simulations d'effets,
réserves, gardes d'opérations et politique de beam ne changent pas. Ces mesures de
coût et de qualité de décision ne mesurent pas un gain de taux de victoire.


### Optimisation du rejeu évolutif (31 octobre)

Lorsqu'une intention connaît déjà sa puce ou son arme, le rejeu prépare seulement les actions de cet item. Chaque préfixe garde un petit cache par item ; le catalogue complet reste disponible pour proposer de nouvelles intentions. Les états et les scores ne traversent toujours pas une action réelle. Aucun scoring ni filtre de légalité ne change.

Témoins sans plafond de banc : 80 tours complets, 983 124 198 → 862 222 922 opérations (−12,30 %), mêmes flux d'actions natifs. 32 recherches isolées ont également les mêmes suites et toutes les mêmes traces de simulation (−10,92 %). 50 contrôles ciblés passent. Ces économies ne démontrent pas à elles seules un gain de victoire contre main.

La recherche tente aussi de valider son meilleur cast local avec le danger avant de préparer la géométrie offensive. Une extension coûteuse ne peut ainsi plus effacer ce choix déjà financé. Régression reproduite sur un poireau à 8 cœurs : un bouclier jouable était perdu au profit du plan inactif ; la correction le conserve, avec un coût global pratiquement identique sur 32 situations. 50 contrats, 25 recalculs indépendants de plans et 20 combats natifs passent sans erreur ni arrêt forcé.


### Candidat figé pour la confirmation du 31 octobre

Le candidat conserve huit plans, 128 tentatives et une seule queue reprise. Les essais à
256/512 tentatives ont augmenté le coût sans gain de force démontré dans le triage. La
réparation systématique des fins illégales a également été écartée : 50 % sur 40 combats,
avec environ 15 % d’opérations supplémentaires par tour de poireau.

Le correctif d’identités passe 50 contrats généraux, 22 contrats natifs de naissance et de
transmission et 25 recalculs indépendants de plans. Sur 32 recherches initiales, réserver et
traduire les identités garde les mêmes suites, simulations et fitness. Le témoin natif
reproduisait deux bulbes réels mais un seul bulbe restant dans la projection ; le correctif
conserve bien les deux. Le pilote de 40 combats de ce seul correctif a donné 47,5 % des points :
il valide le fonctionnement, sans démontrer un gain de victoire.

Conserver les nouveaux départs malgré une suite reprise donne ensuite 16 victoires, 11 nuls
et 13 défaites sur 40 nouveaux combats, soit 53,75 % des points (intervalle apparié à 95 % :
47,5–61,25 %). Son coût par tour de poireau augmente d’environ 1 %. Ce petit échantillon ne
prouve toujours pas une supériorité.

La confirmation est figée avant ses résultats : 200 nouveaux combats miroir contre le main
`3cb1123`, puis 100 contre une copie de ce main dont seule la réservation des identités est
corrigée. Ce deuxième témoin distingue le correctif commun de l’architecture de recherche.
Aucun réglage n’est changé entre ces comparaisons. **Main reste inchangé pendant les tests.**

### Résultats finaux du 31 octobre et limite de budget corrigée

Le candidat figé `643c69b` termine les **200 combats contre main** avec **80 victoires,
50 nuls et 70 défaites**, soit **52,5 % des points** (intervalle bootstrap des paires
miroir à 95 % : **49,25–55,75 %**). Les **100 combats contre main avec identités corrigées**
donnent **42 victoires, 26 nuls et 32 défaites**, soit **55 %** (intervalle **50–60 %**).
Ces résultats ne démontrent pas une supériorité : **main reste `3cb1123`**.

Sur les 100 mêmes scénarios, le premier témoin donne 54,5 % au prototype, contre 55 %
avec le correctif commun ; un seul résultat change et 96 flux d’actions sont identiques.
Comparer directement les 52,5 % et 55 % mélangerait deux échantillons différents.
Face à main, le prototype utilise environ 3,17 % d’opérations en moins par tour de
poireau, joue 4,28 casts contre 3,95 et reste sans cast dans 2,54 % des tours contre
2,89 %. Les trajectoires diffèrent : ces chiffres ne prouvent pas une économie à
actions identiques, ni que tous les tours sans cast soient des erreurs.

Un dépassement natif d’opérations apparaît sur le scénario `034-left`, également dans
le contrôle : c’est le même incident, pas deux cas indépendants. Après la fin complète
des deux campagnes, la boucle d’enregistrement des racines reçoit le garde `room`
déjà utilisé par les extensions. Aucun budget, réserve ou score ne change.
Le rejeu du code figé reproduit exactement le flux fautif ; le même scénario corrigé
termine sans erreur ni arrêt forcé. Les 32 recherches fixes conservent toutes leurs
suites et fitness, pour environ 0,005 % d’opérations supplémentaires. 50 contrats et
25 recalculs complets passent. Ce correctif est **postérieur aux 300 combats** : ceux-ci
ne mesurent donc pas le taux de victoire du code corrigé.

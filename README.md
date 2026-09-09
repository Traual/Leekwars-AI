# IA LeekWars — New_AI

## Présentation

IA LeekWars orientée **simulation** : elle virtualise le combat, cherche une suite d'actions
par exploration, exécute la première action réelle, puis **replanifie** sur l'état moteur réel
après chaque action. Entrée principale : [`New_AI/Main.leek`](New_AI/Main.leek).

**Statut : Bêta.**

## Architecture

- **`StateClass.Virtualize`** capture l'état moteur (entités, stats, effets, cellules) dans un
  State simulé — une map `id -> EntityState` — sur laquelle toute la recherche travaille.
- **Clone-on-write** : les entités du State ne sont clonées que lorsqu'une branche les modifie
  (`ensureCloned` + `clonedIds`), le reste est partagé entre nœuds.
- **`ActionsClass`** génère les actions possibles (items × cibles × cellules de lancer),
  dédoublonnées par ensemble de cibles.
- **`NodeClass`** porte un nœud de recherche (State + hash + termes de score patchés) ;
  **`ConsequencesClass`** simule l'application d'un cast : dégâts, effets persistants
  (ledger à neuf champs), kills et cascades, passives, push/attract, résurrection — les
  mécaniques couvertes suivent le moteur, sous réserve des limites documentées plus bas.
- **`ActionSuite`** : recherche arborescente gloutonne — toutes les actions racines sont
  évaluées, puis chaque branche déroule récursivement son meilleur successeur (`unroll`).
  Ce n'est pas un BFS exhaustif, malgré le nom historique du fichier ; l'IA exécute la
  première action de la meilleure suite puis replanifie.
- **Danger / Heal / Delta** projettent la pression future : ce que chaque ennemi peut infliger
  (Danger), ce que chaque allié peut soigner (Heal), agrégés en net séquencé par ordre de tour
  (Delta) — c'est la « pression » consommée par le TTK du scoring.
- **Quatre PALIERS de déplacement**, dans Danger comme dans Heal. Un caster choisit une
  position ET une panoplie : marcher ne coûte rien, sauter coûte 4 TP, se téléporter 9, les
  deux 13. Chaque palier a donc sa propre géométrie de couverture, et le combo joué depuis une
  cellule reçoit `TOTAL_TP` moins le prix du palier SUFFISANT pour l'atteindre — déduire un
  coût uniforme de toute la couverture serait faux, une cible à portée de marche ne coûtant
  rien. Saut posant `RAW_BUFF_AGILITY 100` dès le cast, les paliers sauteurs portent aussi
  leur propre classe de critique. Les paliers étant emboîtés, ils sont dédoublonnés par hash
  de CONTENU et par classe de critique : une seule évaluation de combo dans le cas courant.
- **Gravity / FinalCell** choisissent le placement de fin de tour : meilleure gravity parmi les
  cellules à net nul, sinon net strictement minimal (sécurité d'abord).
- **Caches et `BaseHash`** : les fonctions coûteuses sont cachées avec des clés qui encodent
  tous leurs inputs ; `BaseHash` (XOR incrémental des contributions `(id, cellule)` des
  entités vivantes hors Me) sert de racine aux clés positionnelles.
- **VM partagée des bulbes** : `summon(chip, cell, Bulb.AI)` passe une fonction de cette IA —
  le bulbe s'exécute dans la VM de l'invocateur (globals + caches partagés). `BulbAI.leek`
  reset donc les caches per-turn avant `InitStateClass` (même ordre que `Main.leek`), et les
  caches persistants dépendants de Me ont Me dans leur clé. Chaque action réelle est suivie
  d'un **replan** complet sur l'état moteur.

## Scoring

Formule réellement active (monnaie : HP d'équipe), par entité vivante :

```
survival = BASE_ALIVE + W_LIFE·sqrt(afterPeriodic) + W_TTK·g + W_TOTAL_LIFE·sqrt(TOTAL_LIFE)
term     = sign × (importance × survival + capability + drDeterrence)
score    = Σ term          (sign = +1 allié, −1 ennemi ; les morts contribuent 0)
```

- **Stock** : `sqrt(afterPeriodic)` — vie après le tick périodique net, concave (finir les
  blessés paie plus par HP).
- **TTK (horizon)** : `g = horizonLife / (horizonLife + TTK_HALF × pressure)` — le seul canal
  où le danger projeté et la traînée périodique nuisible entrent dans la valeur ; pente
  maximale près des seuils de kill.
- **Capital durable** : `sqrt(TOTAL_LIFE)` — seul canal de l'érosion/nova et des buffs de
  vitalité.
- **Six flux périodiques distincts** : POISON / POISON_OVER_TIME, AFTEREFFECT /
  AFTEREFFECT_OVER_TIME, HEAL / HEAL_OVER_TIME. Le tick net = POISON + AFTEREFFECT − HEAL ;
  la traînée nuisible restante = max(0, coefAntidote·POT + AFT_OT − HEAL_OT) — le coef
  antidote ne porte que sur le vrai poison.
- **Capability** : valeur live des buffs, debuffs et shields (Importance.leek).
- **Importance** : figée pendant un replan/BFS (classement stable des cibles), rafraîchie à
  chaque replanification réelle.
- **`CAST_COST`** : coût d'action porté par le chemin, soustrait du score du nœud (le score
  n'est donc pas strictement Σ termes).
- **FinalCell** : meilleure gravity parmi les cellules à net nul, sinon net strictement
  minimal.

**Nuance importante** : `BASE_ALIVE` appartient à `survival`, et `survival` est multiplié par
`importance`. `BASE_ALIVE` apporte donc une **contribution fixe d'être vivant** de
`importance × BASE_ALIVE` au terme de l'entité ; la valeur complète retirée par un kill
contient aussi les autres composantes de survival, capability et drDeterrence — capability
pouvant être négative, ce n'est pas un plancher universel strict. Ne jamais écrire qu'un
kill vaut systématiquement au moins 1000 dans le score final : une faible importance rend
volontairement un summon ou une entité faible moins précieux.

## Invariants techniques

- Un `return` vide doit **toujours** être écrit `return;` — un `return` nu fait avaler par le
  parser LeekScript l'expression de la ligne suivante.
- Aucun objet provenant d'un cache ne doit être muté en place.
- Tout nouvel input lu par une fonction cachée doit être ajouté à sa clé **le jour même**.
- Les effets persistants utilisent le ledger moteur à huit champs, plus un neuvième propre à
  l'IA :
  `[type, value, casterFId, turns, critical, itemId, targetFId, modifiers, ticksDatés]` — le
  neuvième porte les ticks encore DATÉS de la ligne, ceux qui ont nourri les agrégats par tour,
  et c'est lui que lit `removeCastedEffect` pour défaire exactement ce qui avait été ajouté.
  Enregistrement, merge, remplacement, suppression et debuff passent par ce format unique.
- Les fichiers Java de recherche présents localement à la racine du dépôt ne font **pas**
  partie de l'IA et ne doivent **jamais** être commités.

## Profiler

- `BenchOps.ENABLED = false` en production ([`Benchmark.leek`](New_AI/Utils/Benchmark.leek)).
- Passer temporairement à `true` pour un combat de mesure, puis remettre `false` après
  validation.
- Éviter les logs de combat systématiques : mesure à chaque lot important seulement.

## Limites et compromis assumés

Documentés comme des choix, pas des bugs à corriger immédiatement :

- Critique **binaire déterministe**, aucune espérance probabiliste intermédiaire : ce qui
  joue pour moi ne critique qu'au seuil de quasi-certitude `MIN_AGILITY_TO_FULLY_CRIT`
  (95 %+) ; ce qui joue contre moi est craint critique dès `MIN_AGILITY_ENEMY_CRIT` (5 %) —
  doctrine pire-cas-pour-moi, symétrique à 5 % près.
- Futurs buffs des non-Me non séquencés : Danger/Heal ne simulent pas une séquence
  préparatoire où une entité dépense TP et cooldowns pour (se) buffer.
- Placement déterministe simplifié des summons et résurrections.
- Multiplicateur Plasma (`EFFECT_MODIFIER_MULTIPLIED_BY_TARGETS`) fixé à 1 dans EntityDanger —
  le vrai facteur dépend de la cellule visée et du nombre d'entités dans l'AoE.
- Futurs ticks de poison sans érosion critique ni futurs procs.
- La LIFE courante d'un caster n'entre pas dans les clés de Danger. Après des dégâts non
  létaux, la projection d'un `EFFECT_LIFE_DAMAGE` peut donc conserver la valeur précédemment
  cachée pendant le tour. Une mort reste immédiatement visible via les contributions
  vivant/mort. Ce compromis évite une invalidation globale de tous les termes adverses à
  chaque branche offensive (l'exactitude a été mesurée à +6,31 % d'opérations, misses
  Danger ×4). Un oracle de HIT, qui recalcule à neuf la valeur servie par les caches de
  danger, ne trouve aucun autre écart. Sans la LIFE dans la clé : 58 515 hits contrôlés sur
  `DANGER_ENTITY_CACHE`, **zéro écart**, et 136 809 sur `COMBO_FROM_ITEM_AOE_CACHE`,
  **36 écarts** — soit 195 324 hits contrôlés au total. En remettant la LIFE dans la clé :
  85 326 hits Danger et 85 266 hits Combo, soit 170 592 au total, **zéro écart**. Remettre
  la LIFE change les clés, donc la trajectoire : les deux runs ne contrôlent pas le même
  ensemble de hits, mais le seul champ retiré entre eux est la LIFE et les écarts
  disparaissent entièrement. Aucun autre champ ne manque donc aux clés.
- Le tableau trié de `ENTITY_SORTED_BY_TURN_ORDER_CACHE` est persistant alors que
  `TURN_ORDER` est relu à chaque virtualisation : il peut être périmé. Le plan de danger
  reconstruit sa séquence par partition sur les ordres COURANTS, ce qui reproduit exactement
  le comportement hybride historique. Corriger la péremption du tri est un chantier distinct.
- Collision théorique de certaines clés de cache au-delà de 4096 de force.
- Cellule de Me **gelée** pendant un BFS pour la couverture Antidote.
- LoS ignorée pour la couverture Antidote.
- Dédoublonnage des actions par ensemble de cibles : un seul `From` conservé par ensemble.
- Mobilité future de Danger ignorant les entités (portées de déplacement sans obstacles
  vivants).
- Bonus des passives BR simulé par l'approximation `POWER / 2`.
- FinalCell strictement orientée sécurité lorsqu'aucune cellule n'a un net nul.
- Transpositions (élagage de nœuds déjà vus) **abandonnées après mesure** : trop peu de
  nœuds pour être rentable.
- Valeurs pessimistes/minimales dans Danger et Heal (jets minimaux, heal amorti par
  `HEAL_COEF`).
- La carte de danger réduit les boucliers d'un débuff sur leur AGRÉGAT, là où le moteur
  réduit et arrondit chaque LIGNE du registre séparément puis somme. `round(a·k) + round(b·k)`
  n'est pas `round((a+b)·k)`, et UNE seule Libération suffit à l'exposer : deux lignes de
  bouclier absolu à 101 conservées à 60 % donnent 61 + 61 = 122 côté moteur, contre
  `round(202·0,6) = 121` côté agrégat. L'échéance, elle, retire bien 122 ligne à ligne, donc
  le bouclier tombe à −1 au lieu de 0 et le coup suivant est sur-crédité d'autant. La chaîne
  ne transportant que deux scalaires, corriger demanderait d'y porter les lignes et de faire
  expirer un CRÉNEAU plutôt qu'un montant. Écart connu et ASSUMÉ, pas une approximation
  neutre. Le chemin de simulation EXACTE (`getEntityDebuffConsequences`), lui, réduit bien
  ligne par ligne.
- `UNHEALABLE` est simulé (refus du vol de vie au lanceur, soins et ticks de soin annulés chez
  le porteur), mais aucun item actuel ne le pose : le chemin ne s'exerce qu'en forçant l'état.
- Ordre de tour approximatif (fin de liste) pour une entité déjà morte et inconnue lors de la
  capture initiale.
- Possible score de résurrection stale après un kill réel au milieu du tour : les clés de
  NETDAMAGE_CACHE / fuite / danger ne pinnent pas le set des vivants du camp adverse, dont
  dépend l'ordre d'un ressuscité (précision de score uniquement, replanifié à chaque action
  réelle).
- IDs virtuels de bulbes incompatibles avec un hypothétique chip de bulbe à cooldown nul :
  id virtuel = `SUMMON_ID_BASE + typeOffset`, un resummon même-type dans une branche
  écraserait le premier bulbe.
- `ACTIONS_ON_ME_CACHE` clé par MyCell seule : insuffisant pour un hypothétique self-item
  avec portée et LoS — cas inexistant actuellement (tous les self-items ont MINRANGE 0).

## Travaux reportés après la Bêta

1. Auditer puis corriger la complétude de `ScoredCache`.
2. ~~Mesurer le gain d'un sac à dos exact contre les combos gloutons Danger/Heal.~~ MESURÉ :
   un sac à dos borné exact coûte 1 522 opérations par appel contre 165 au glouton, soit
   **+39,2 % des opérations de toute l'IA**. Rejeté. Le glouton n'est pas monotone en l'ensemble
   d'items (ajouter un item peut dégrader la pile retenue), ce qui reste le vrai résidu.
3. Instrumenter les composantes du scoring si un réglage des poids devient nécessaire.

# Diagnostic d'une décision

Outil de lecture, pas de mesure : il sert à comprendre POURQUOI une décision a été prise, pas à
décider si elle est bonne. Un petit lot de combats ne départage aucune version.

Le raccordement vit dans l'IA (`Scoring/Scoring.leek`, `User/BFS.leek`, `User/FinalCell.leek`) et
il est **éteint dans le dépôt** : `DIAG_TURN = 0`. Éteint, il coûte une comparaison par décision
et deux tests par évaluation de placement. Seule la copie déployée par la sous-commande
`decision` arme les constantes, sur un seul tour d'une seule entité.

## Les trois commandes

```bash
python training/diagnostics/decision.py combats training/runs/diagnostics/lot-1 --graine 424242 --combats 2
```

Joue les blocs éleveur aux cœurs réels contre le champion (`--reference <commit>` pour une autre
référence), chaque bloc dans les deux orientations, archive pour chaque combat le journal
d'actions du moteur et les journaux des IA — **et archive le bundle joué** sous `<travail>/bundle`.

```bash
python training/diagnostics/decision.py situations training/runs/diagnostics/lot-1 --combien 20
```

Classe les tours de nos poireaux par ce qu'ils ont PAYÉ pour ce qu'ils ont obtenu : dégâts
infligés pendant le tour, moins ce qu'ils encaissent jusqu'à leur tour suivant. Les morts
d'abord. C'est une liste de suggestions — une défaite n'est pas une erreur.

```bash
python training/diagnostics/decision.py decision training/runs/diagnostics/lot-1 --combat 1-gauche --tour 2 --entite 3 --sans-item 84
```

Rejoue LE MÊME scénario — même composition, même graine, mêmes cœurs — **depuis le bundle
archivé**, jamais depuis l'arbre de travail : sans cette garantie, rien ne dit que le diagnostic
décrit le code qui a joué le combat. Le rejeu échoue si l'archive manque ou a été altérée.

Il rend, pour chaque replanification du tour visé :

- le contexte : tour, budget, et une ligne par entité vivante (camp, cellule, vie, ressources,
  caractéristiques, flux, et sa valeur pour le score) ;
- la suite retenue, puis jusqu'à cinq autres **têtes de chaîne** réellement explorées depuis le
  même état — pour chacune, sa meilleure suite. On compare des suites entières depuis le même
  état de départ ; le score d'une suite n'est jamais prêté à sa première action ;
- avec `--sans-item <id>`, la meilleure suite explorée qui ne lance **aucun** cast de cet item :
  une vraie concurrente, scorée par les mêmes coefficients, au lieu de retrancher une
  contribution à la main. Les identifiants d'item sont dans le texte des suites (`nom#id`) ;
- pour chaque suite : score brut, gain, et les contributions par entité et par statistique,
  calculées par `ScoringClass.entityValue` elle-même ;
- la pénalité de placement **quand elle a réellement été calculée**, avec son mode — `danger
  calcule`, `memo` (résultat complet servi par le cache) ou `spatial seul`. Une suite non close
  annonce son statut (`refusee faute de budget`, `tronquee`, `non evaluee : ...`) et n'affiche
  aucune pénalité : une pénalité absente n'est pas une pénalité nulle ;
- le placement réellement joué en fin de tour, sous la même forme.

Rien n'est réévalué pour le diagnostic : les statuts viennent du reclassement terminal qui vient
d'avoir lieu.

`--entite` prend un identifiant, **0 compris** : la sentinelle « toutes les entités » est `-1`.

## La vérification de trajectoire

L'instrumentation coûte des opérations, et des opérations changent ce qu'un tour a le temps de
faire. Le diagnostic parle à CHAQUE replanification du tour, donc deux contrôles distincts :

```
prefixe identique au combat normal (256 actions) : la PREMIERE decision du tour est validee
tour complet identique (indices 256 a 268) : TOUTES les decisions du tour sont validees
```

Le préfixe seul ne valide que la première décision — les suivantes se jouent après la première
émission de diagnostic, donc sous un budget déjà entamé. Une cible qui ne joue pas au tour
demandé est annoncée `CIBLE ABSENTE`, jamais confondue avec un préfixe identique.

## Calibration de la pénalité d'exposition

```bash
python training/diagnostics/exposition.py training/runs/diagnostics/lot-1 training/runs/diagnostics/lot-2
```

Sur les combats déjà archivés, sans aucune instrumentation : ce qu'un net projeté coûte
réellement à la cellule de fin de tour retenue, pour les deux camps.

Deux pièges que cette sonde évite, et qu'il faut connaître avant de lire un ratio :

- **L'entité journalisée n'est pas l'entité qui joue.** Le premier champ d'une ligne de log porte
  l'identifiant de la VM : un bulbe écrit sous son invocateur. Dans un combat du lot, `hardleeker`
  porte 354 lignes `Best final cell` pour 62 tours joués — les 292 autres sont celles de ses
  quatorze invocations. Chaque ligne est donc rattachée à la **fenêtre de tour qui contient son
  indice d'action**, jamais au n-ième tour d'une entité : un placement manquant décalerait sinon
  tous les suivants.
- **Le net n'est pas les dégâts encaissés.** `DeltaClass.netDamage` déroule l'ordre de tour en
  retranchant les dégâts ET en ajoutant les soins et la vitalité projetés des alliés, plafonne à
  la vie max, puis rend `max(0, vie_avant − vie_après)`. La quantité comparable est donc la
  **variation nette de vie** ; les dégâts directs, les flux périodiques et les soins reçus sont
  rendus à part.

Deux censures sont annoncées séparément et jamais fondues dans le ratio : une entité qui meurt
avant son tour suivant perd au plus la vie qui lui restait alors que le net compte l'overkill, et
une fenêtre tronquée par la fin du combat est écartée.

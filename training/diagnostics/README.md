# Diagnostic d'une décision

Outil de lecture, pas de mesure : il sert à comprendre POURQUOI une décision a été prise, pas à
décider si elle est bonne. Un petit lot de combats ne départage aucune version.

Le raccordement vit dans l'IA (`Scoring/Scoring.leek`, `User/BFS.leek`, `User/FinalCell.leek`) et
il est **éteint dans le dépôt** : `DIAG_TURN = 0`. Éteint, il coûte une comparaison par décision
et deux tests par évaluation de placement. Seule la copie déployée par la sous-commande
`decision` arme les deux constantes, sur un seul tour d'une seule entité.

## Les trois commandes

```bash
python training/diagnostics/decision.py combats training/runs/diagnostics/lot-1 --graine 424242 --combats 2
```

Joue les blocs éleveur aux cœurs réels contre le champion (`--reference <commit>` pour une autre
référence), chaque bloc dans les deux orientations, et archive pour chaque combat le journal
d'actions du moteur et les journaux des IA.

```bash
python training/diagnostics/decision.py situations training/runs/diagnostics/lot-1 --combien 20
```

Classe les tours de nos poireaux par ce qu'ils ont PAYÉ pour ce qu'ils ont obtenu : dégâts
infligés pendant le tour, moins ce qu'ils encaissent jusqu'à leur tour suivant. Les morts
d'abord. C'est une liste de suggestions — une défaite n'est pas une erreur.

```bash
python training/diagnostics/decision.py decision training/runs/diagnostics/lot-1 --combat 0-droite --tour 11 --entite 7
```

Rejoue LE MÊME scénario — même composition, même graine, mêmes cœurs, seuls les chemins d'IA de
notre camp pointent la copie armée — et rend, pour chaque replanification du tour visé :

- le contexte : tour, budget, et une ligne par entité vivante (camp, cellule, vie, ressources,
  caractéristiques, flux, et sa valeur pour le score) ;
- la suite retenue, puis jusqu'à cinq autres **têtes de chaîne** réellement explorées depuis le
  même état — pour chacune, sa meilleure suite. On compare des suites entières depuis le même
  état de départ ; le score d'une suite n'est jamais prêté à sa première action ;
- pour chaque suite : score brut, gain, et les contributions par entité et par statistique,
  calculées par `ScoringClass.entityValue` elle-même ;
- la pénalité de placement **quand elle a réellement été calculée**, avec son mode — `danger
  calcule`, `memo` (résultat complet servi par le cache) ou `spatial seul`. Une suite non close
  annonce son statut (`refusee faute de budget`, `tronquee`, `non evaluee : ...`) et n'affiche
  aucune pénalité : une pénalité absente n'est pas une pénalité nulle ;
- le placement réellement joué en fin de tour, sous la même forme.

Rien n'est réévalué pour le diagnostic : les statuts viennent du reclassement terminal qui vient
d'avoir lieu.

## La vérification de trajectoire

L'instrumentation coûte des opérations, et des opérations changent ce qu'un tour a le temps de
faire. La sous-commande `decision` compare donc le journal d'actions du moteur, du début du
combat jusqu'au tour visé, avec celui du combat normal, et l'annonce en première ligne :

```
trajectoire identique au combat normal jusqu'a la decision (1718 actions)
DIVERGENCE due a l'instrumentation avant la decision : action 214 : [...] contre [...]
```

Une décision lue après une divergence ne décrit pas le combat de référence.

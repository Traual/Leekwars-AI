# Journal de validation du scoring

Ce fichier ne conserve que les mesures qui ont influencé une décision. Les rapports JSON
complets restent locaux dans `training/results/` : ils contiennent les builds, graines et
détails par paire nécessaires à une reprise, mais sont trop volumineux et trop nombreux pour
être versionnés.

Snapshot public : top 50 solo et top 50 éleveurs collectés le 12 août 2026. Tous les combats
sont des paires miroir, avec 1 024 cœurs minimum pendant la sélection. Générateur au commit
`4e31b61`, JAR SHA-256 `859f6b99a6140ae4343d36023b0fabe99cfbb4d37750eb8d5e100d0a06958186`.

## Sorties de placement

| Candidat | Paires | Tours max | Fitness moyenne | Erreur-type | Décision |
|---|---:|---:|---:|---:|---|
| cible ennemi 5 / allié 3 | 64 | 6 | +0,0028 | 0,0351 | rejeté, neutre |
| cible allié 3 seulement | 24 | 8 | −0,1054 | 0,0871 | rejeté |
| danger × 0,50 | 24 | 8 | −0,1079 | 0,0689 | rejeté |
| danger × 0,75, sélection | 24 | 8 | +0,1502 | 0,0659 | candidat de sélection |
| danger × 0,875, sélection | 24 | 8 | +0,0807 | 0,0497 | candidat secondaire |
| danger × 0,75, holdout frais | 64 | 20 | −0,0076 | 0,0506 | rejeté, 53–53 |
| danger × 0,875, validation fraîche | 64 | 8 | +0,0546 | 0,0369 | confirmation requise |
| danger × 0,875, confirmation fraîche | 128 | 8 | +0,0088 | 0,0277 | rejeté, 56–59 |

Le beau résultat du facteur 0,75 sur les graines de sélection disparaît entièrement sur le
holdout long : il ne doit pas entrer dans le modèle. C'est exactement la raison d'être du
découpage sélection/validation et des graines figées.

Le facteur 0,875 reproduit le même phénomène en plus lent : +0,0546 sur une première validation,
puis +0,0088 sur deux fois plus de paires fraîches. Aucun des réglages scalaires de `FinalCell`
testés ici ne justifie donc une modification du modèle de production.

La cible ennemi 5 / allié 3 a aussi provoqué un dépassement réel du plafond de 256 M sur une
branche, alors que le miroir de référence terminait. Le même échantillon à 1 024 cœurs a fini
sans erreur et a montré que le poids était neutre. Les hauts cœurs servent donc bien à ne pas
tronquer l'apprentissage ; le stress à 256 reste un contrôle de déploiement séparé.

`total_operations_diagnostic` n'a servi à rejeter aucun poids. Les candidats ci-dessus ne
changent pas l'inférence : les sorties du MLP sont déjà calculées. Le facteur de danger ne
réveille en plus aucun canal conditionnel, contrairement à `HEAL_COST`.

## Contrôles du harnais

- Parité solo, une paire : fitness `0`, ratio total d'opérations `1,000`.
- Parité BR à dix, une paire : fitness `0`, ratio total d'opérations `1,000`.
- Profil exact 4v4, une paire : scoring `3 508` ops/appel, FinalCell `3 663` ops/appel,
  tour complet `12,86 M` ops ; ratios candidat/référence `1,000` pour chaque section.
- Dix tests statiques couvrent dataset, layout du vecteur, injection JSON, découverte du JDK,
  déterminisme des paires, contextes farmer/BR, cohortes de profilage et fitness bornée.

## Tête hybride de première transition

La tête est séparée du checkpoint de 976 paramètres et reste `null` dans l'IA versionnée. Elle
produit douze coefficients depuis le plongement global du replan (108 paramètres au total), puis
note uniquement les conséquences de la première action. L'arbre et son score terminal restent
présents ; le bonus racine est simplement transporté dans les descendants.

Probe neutre du 19 août 2026 : une paire miroir éleveur, deux tours max, vecteur de transition
entièrement nul mais chemin réellement exécuté et profilé.

- fitness `0`, deux combats nuls et mêmes nombres d'appels dans toutes les sections ;
- coût total candidat/référence `1,0329` ;
- `Transition` : `511,2` opérations par action racine, 4 199 appels ;
- `Scoring` hors tête : ratio `1,000` et `3 258,3` opérations/appel ;
- aucune erreur d'IA ni dépassement.

Budget fixé avant sélection : cible au plus `2×`, rejet absolu au-dessus de `3×`. Le probe neutre
valide donc largement le coût intrinsèque, mais ne prouve encore aucun gain de qualité.

Premier holdout frais de `CAPABILITY=0,25`, figé avant lecture des résultats : 64 paires miroir
éleveur, huit tours max, graine de sélection `2026081925`.

- fitness moyenne `+0,0286`, erreur-type `0,0364`, IC 95 % `[−0,0428 ; +0,0999]` ;
- 27 victoires candidat, 23 victoires référence et 78 nuls ;
- coût total candidat/référence `1,1163` ;
- aucune erreur d'IA ni dépassement.

Décision préenregistrée appliquée : le signe positif mais l'intervalle ambigu ne suffit pas à
promouvoir le coefficient. Aucun réglage n'est effectué sur ce holdout ; une confirmation sur
de nouvelles graines est requise.

Confirmation indépendante : 128 nouvelles paires, graine `2026081926`, sans graine de combat
commune avec le premier holdout.

- fitness moyenne `+0,0333`, erreur-type `0,0310`, IC 95 % `[−0,0273 ; +0,0940]` ;
- 63 victoires candidat, 53 victoires référence et 140 nuls ;
- coût total candidat/référence `1,1113` ;
- aucune erreur d'IA ni dépassement.

Les 192 paires combinées donnent `+0,0318`, erreur-type `0,0239`, IC 95 %
`[−0,0151 ; +0,0786]`. La règle d'arrêt fixée avant la confirmation exigeait une moyenne de
confirmation positive et une borne basse combinée strictement positive. La première condition
est remplie, la seconde non : `CAPABILITY=0,25` est rejeté comme amélioration non prouvée. Le
signal reste utile comme point de départ d'une tête multivariée, mais il ne doit pas être activé
seul en production.

### Entraînement orthogonal multivarié

Un plan de Hadamard à huit politiques a ensuite fait varier sept biais simultanément sur les
mêmes 16 paires (`selection_seed=2026082001`). L'estimation retire un intercept propre à chaque
paire avant d'agréger les effets, puis rétrécit les directions incertaines vers zéro. Ces données
sont de l'entraînement, pas un holdout.

Les moyennes des huit politiques vont de `−0,2279` à `+0,0375` ; aucune ne constitue une preuve.
Une politique brute a en outre atteint un ratio comportemental d'opérations `3,785×` et est donc
hors du plafond absolu, indépendamment de sa fitness. La seule direction nette du plan était
`SHIELD` négative (`effet −0,0610 ± 0,0514` à 95 %, `t=−2,33`). Après rétrécissement, le vecteur
sparse exporté portait les biais suivants :

- `CAPABILITY=−0,1121`, `PERIODIC=−0,0215`, `SHIELD=−0,8439` ;
- `ALIVE=+0,0102`, `TP_LEFT=−0,4353`, `COST=+0,3920`, `COOLDOWN=+0,1144` ;
- tous les autres paramètres de la tête à zéro.

Holdout frais du vecteur appris : 64 paires éleveur, huit tours max, graine `2026082002`.

- fitness moyenne `−0,0665`, erreur-type `0,0541`, IC 95 % `[−0,1726 ; +0,0395]` ;
- 30 victoires candidat, 33 victoires référence et 65 nuls ;
- coût total candidat/référence `1,3318` ;
- aucune erreur d'IA ni dépassement.

La règle fixée avant le holdout rejetait toute moyenne non positive. Le vecteur multivarié est
donc rejeté et n'est testé ni en solo ni en battle royale. Aucun modèle de transition n'est
activé dans l'IA versionnée.

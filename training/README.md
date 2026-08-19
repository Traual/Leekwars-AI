# Environnement local d'entraînement

Ce dossier compare un candidat au checkpoint courant dans le générateur officiel Leek Wars.
Chaque exemple est une **paire miroir** : mêmes builds, même carte et même graine ; seule
l'affectation candidat/référence aux deux camps est inversée. Le mode prioritaire est le 4v4
éleveur, puis le solo. Le harness injecte aussi explicitement `getFightType()` et
`getFightContext()` : le parseur JSON générique du générateur ne le fait pas lui-même.

Les expériences qui ont réellement conduit à accepter ou rejeter un réglage sont résumées dans
[`RESULTS.md`](RESULTS.md), y compris les résultats négatifs pour ne pas les redécouvrir.

## Données publiques

`collect_meta_builds.py` collecte les 50 premiers classements actifs solo et éleveur, puis les
stats finales et les items publics des poireaux concernés :

```bash
python3 training/collect_meta_builds.py --generator ../leek-wars-generator
```

Le résultat `training/data/meta_builds.jsonl` contient un manifeste, les deux classements et un
enregistrement par build unique. Les réponses brutes sont reprises depuis
`training/data/.api-cache/` après une coupure. Aucune authentification n'est utilisée.

Les stats injectées sont les champs `total_*` de l'API : ils comprennent le capital et les
composants. Les armes et puces utilisent leur identifiant de template public, format attendu
par le générateur.

## Benchmark miroir

Le [générateur officiel](https://github.com/leek-wars/leek-wars-generator) doit être cloné à
côté du dépôt et son `generator.jar` doit être construit. Le harness cherche d'abord un JDK
dans `../tools/jdk`, puis celui du `PATH`; `--java-home` permet de fixer explicitement son
emplacement.

```bash
git clone --recurse-submodules https://github.com/leek-wars/leek-wars-generator ../leek-wars-generator
(cd ../leek-wars-generator && ./gradlew jar)
```

Test de parité du checkpoint contre lui-même :

```bash
python3 training/run_benchmark.py \
  --mode farmer --pairs 2 --max-turns 2 --name parity
```

Activation expérimentale de sorties du MLP déjà câblées dans `FinalCell` :

```bash
python3 training/run_benchmark.py \
  --mode farmer --pairs 20 --max-turns 6 --jobs 2 \
  --bias NET=0.5,TARGET_DISTANCE=4 --name final-cell-01 \
  --output training/results/final-cell-01.json
```

Un entraînement complet peut exporter soit un tableau JSON de 976 nombres, soit un objet
`{"vector": [...]}`. Il se teste sans toucher au modèle de production :

```bash
python3 training/run_benchmark.py \
  --mode farmer --pairs 20 --max-turns 10 \
  --model-vector training/models/candidate.json --name trained-model
```

### Tête hybride de première transition

Le score terminal de l'arbre reste intact. Une tête séparée de 108 paramètres peut ajouter un
bonus à la première action réellement exécutée, puis ce bonus est transporté sans recalcul dans
toute sa branche. Elle est `null` dans l'IA versionnée : aucun coût ni changement de comportement
en production. Un benchmark l'active seulement dans sa copie candidate :

```bash
python3 training/run_benchmark.py \
  --mode farmer --pairs 8 --max-turns 4 \
  --transition CAPABILITY=0.25 --name transition-capability-025
```

Les douze biais disponibles sont `DELTA`, `EFFICIENCY`, `CAPABILITY`, `PERIODIC`, `LIFE`,
`MAX_LIFE`, `SHIELD`, `ALIVE`, `TP_LEFT`, `MP_LEFT`, `COOLDOWN` et `COST`. Passer par exemple
`--transition DELTA=0` injecte un vecteur entièrement nul : la tête tourne réellement et sert à
mesurer sa parité et son coût intrinsèque.

Un petit entraînement de politique évite de choisir ces biais un par un. Huit variantes issues
d'un plan de Hadamard font varier sept directions à la fois sur les mêmes paires miroir. Les
effets sont estimés séparément par paire, puis les directions incertaines sont rétrécies vers
zéro avant d'exporter un vecteur de 108 paramètres sparse :

```bash
python3 training/train_transition.py \
  --pairs 16 --max-turns 8 --selection-seed 2026082001 \
  --workers 2 --generator ../leek-wars-generator
```

Les rapports intermédiaires sont reprenables. Le candidat final est écrit dans
`training/results/transition-orthogonal/trained-transition.json` et doit obligatoirement être
testé sur une graine fraîche avec `--transition-vector` ; les paires du plan ne constituent pas
un holdout.

Les surcharges `--bias` et `--index` sont appliquées après ce vecteur, ce qui permet d'isoler
une sortie avant de réexporter le modèle définitif.

Sorties disponibles :

| Biais | Effet |
|---|---|
| `DISTANCE` | facteur additif au multiplicateur de distance par entité |
| `COVID` | facteur additif au multiplicateur de contagion |
| `TARGET_DISTANCE` | distance visée en cases, canal historiquement nul |
| `NET` | facteur additif au multiplicateur du danger net |
| `HEAL_COST` | coût de soin consommé, canal historiquement nul |
| `CENTER` | facteur additif au coût du centre |
| `CROWDING` | facteur additif au coût de regroupement |

Une sortie multiplicative brute de `0.5` donne un facteur `1.5`; une sortie nulle conserve
exactement le poids historique. `TARGET_DISTANCE` et `HEAL_COST` sont additifs et positifs.

## Budget d'opérations

Les scénarios d'entraînement donnent au minimum **1 024 cœurs** à chaque poireau. Cela
n'agrandit pas l'arbre de
recherche : l'IA ne lit pas le nombre d'opérations déjà consommées. C'est uniquement un plafond
artificiel de sécurité pour que le moteur n'interrompe pas l'entraînement. Une validation
séparée avec `--min-cores 256` contrôle ensuite que le candidat reste déployable sous le plafond
réel visé ; elle ne sert pas à choisir ses poids.

Le ratio `total_operations_diagnostic` du rapport mélange coût intrinsèque et changement de
comportement : de meilleurs poids peuvent rendre un combat plus serré, changer les branches
explorées ou allonger le match. Il reste un garde-fou : cible `≤2×`, rejet absolu au-dessus de
`3×`. Le profil `--profile-ops` isole en plus le coût intrinsèque du scoring pour un travail
identique. Modifier les 976 poids déjà évalués par le MLP n'ajoute aucune inférence ; seul un
canal conditionnel comme `HEAL_COST` ou la tête de transition doit être profilé séparément.

`--profile-ops` active `BenchOps` uniquement dans les copies temporaires du candidat et de la
référence. Le rapport donne alors le coût exact moyen par appel de `Scoring`, `FinalCell` et du
tour complet, sans modifier l'IA de production.

Le parallélisme est volontairement limité à deux JVM (`--jobs 1` par défaut, `--jobs 2` au
maximum) afin de ne pas faire concourir plusieurs générateurs gourmands en mémoire.

Un criblage séquentiel et reprenable des sorties de placement est fourni :

```bash
python3 training/sweep_final_cell.py --pairs 2 --max-turns 2
```

Chaque candidat écrit son rapport avant le suivant. Relancer la commande reprend les rapports
déjà terminés au lieu de les recalculer.

Le criblage sert uniquement à choisir une hypothèse. Le candidat retenu doit ensuite être
mesuré sur des graines qui n'ont participé à aucun choix, avec des combats plus longs :

```bash
python3 training/run_benchmark.py \
  --mode farmer --pairs 64 --max-turns 20 --selection-seed 20260816 \
  --jobs 2 --bias NET=-0.25 --name farmer-holdout \
  --output training/results/farmer-holdout.json
```

Après ce holdout éleveur viennent, dans l'ordre, un holdout solo, une BR à dix, un stress test
avec `--min-cores 256`, puis `--profile-ops`. Changer à nouveau les poids à partir d'un de ces
holdouts le transforme en données d'entraînement : il faut alors réserver une nouvelle graine
pour la validation finale.

Quand `--output` est fourni, `run_benchmark.py` écrit également un checkpoint `.partial` après
chaque lot. Une relance avec les mêmes données, modèle et paramètres ne recalcule que les lots
manquants.

Une direction précise du vecteur peut compléter les biais après un point-virgule, par exemple
`--candidate 'target-rel:TARGET_DISTANCE=5;897=-2'` (ennemis à 5 cases, alliés à 3).

Le mode `--mode br` construit dix équipes d'un poireau. Cinq utilisent le candidat et cinq la
référence, puis les affectations sont inversées dans le miroir ; la fitness compare la survie
des deux cohortes et le gagnant doit appartenir à la bonne cohorte.

Les invariants du dataset, du layout du modèle et des scénarios se vérifient sans lancer de JVM :

```bash
python3 training/test_harness.py
```

# Harnais d'amélioration du scoring

Laboratoire local : proposer un changement de scoring, le tester par étapes, garder les
résultats, et promouvoir un champion seulement quand une règle figée d'avance le permet.

**Ce dépôt-ci ne lance aucune boucle.** `run-loop` refuse de démarrer sans un drapeau
explicite, et la commande n'est de toute façon pas implémentée : la livraison porte sur le
harnais, pas sur son exécution.

## Ce qui est construit, et ce qui ne l'est pas

| Élément | État |
|---|---|
| Branche `scoring`, commit de traçabilité, tag `scoring/champion-000` | fait |
| `bundle.py` — empreinte reproductible, matérialisation en lecture seule | fait |
| `scenarios.py` — formats, compositions, blocs de quatre combats, plans de graines | fait |
| `evaluator.py` — JVM par worker, clé de cache complète, classement des erreurs | fait |
| `statistics_lab.py` — agrégats par blocs, Welch–Satterthwaite, décision figée | fait |
| `registry.py` — SQLite, journal de publication, garde du champion attendu | fait |
| `optimizer.py` — contrat d'optimiseur, mode manuel, contrôle de périmètre | fait |
| `cli.py doctor` et `cli.py calibrate` | fait, mesuré |
| `cli.py init-campaign / register-candidate / evaluate / report / resume` | **non fait** |
| Orchestration des blocs et remplissage du cache C/O et H/O | **non fait** |
| Écriture Git d'une promotion (commit, tag, pointeur) | **non fait**, le journal SQLite l'attend |
| Audit BR | **non fait** |

Les tests de réception couvrent ce qui existe. Ceux qui exigent `evaluate` — rejeu à
l'identique, reprise après interruption, invalidation d'un cache de combats réel — ne sont pas
simulés : un test qui ne joue aucun combat ne prouverait rien sur le cache de combats.

## Commandes

```bash
python training/cli.py doctor
python training/cli.py calibrate --time-budget 540 --formats solo,farmer --par-format 6
```

`doctor` vérifie le moteur, le JDK, la concordance du champion avec son manifeste, les builds,
les formats et la ligue. `calibrate` joue de vrais combats aux cœurs réels et écrit
`runs/rapport-debit.json`.

La commande d'une première campagne bornée, quand l'orchestration existera :

```bash
python training/cli.py init-campaign --config training/config/loop.yaml \
  && python training/cli.py evaluate --stage screen --wave 1 \
  && python training/cli.py report --wave 1
```

## Débit mesuré

Un worker, cœurs réels des builds, combats jusqu'à la limite normale de 64 tours.

| format | combats | exécution médiane | p90 | max | combats/minute |
|---|---:|---:|---:|---:|---:|
| solo | 6 | 2,31 s | 3,59 s | 4,70 s | 21,6 |
| farmer | 6 | 7,25 s | 11,03 s | 20,44 s | 6,6 |

Le premier lot paie une compilation froide d'environ 2,2 s, mesurée lors d'un essai antérieur ;
les lots suivants réutilisent le cache du générateur et affichent zéro. Le débit à deux workers
n'est pas mesuré. Les combats éleveur portent des tours avortés, ce qui est le comportement
réel du moteur au plafond d'opérations et reste dans l'échantillon.

Ordre de grandeur avec ces chiffres : une vague de quatre candidats jusqu'au finaliste
représente environ 300 combats candidats, soit à peu près 45 minutes en éleveur à un worker,
références déjà en cache. Une confirmation de 280 blocs représente 1 120 combats. Ce sont des
dépenses rendues visibles, pas des prédictions de durée.

## Deux pièges rencontrés pendant la construction

**Le chemin d'IA doit être relatif à la racine du générateur.** Le `NativeFileSystem` du
compilateur LeekScript résout depuis sa propre racine. Un chemin absolu ne provoque aucune
erreur de lancement : le combat se joue et l'IA lève à chaque tour. Symptôme mesuré, 128
erreurs par combat, 65 tours, quatre dixièmes de seconde. Sans regarder le détail, on prend ça
pour un débit exceptionnel. Les bundles sont donc matérialisés sous
`<générateur>/test/ai/bundles/<empreinte>/`.

**Le nom du répertoire d'un bundle est son empreinte.** Le générateur ressert un binaire
compilé quand un nom a déjà servi. Nommer par l'empreinte rend ce comportement correct : un nom
identique signifie un contenu identique.

## Ce qui vient de l'ancien harnais

Repris de `agent/training-harness-meta-builds` (`f7804cf`) :

- `tools/BatchRunner.java` — une JVM pour plusieurs scénarios, et surtout l'injection explicite
  de `fight_type` / `fight_context`, que `Scenario.fromFile` ne désérialise pas.
- `data/meta_builds.jsonl` et `collect_meta_builds.py` — le snapshot de builds.
- `RESULTS-2026-08.md` — journal historique, conservé comme trace datée. **Ses scores ne sont
  pas des mesures du champion actuel** et ne doivent pas être recyclés comme telles.

Repris de `agent/hybrid-transition-scoring` (`7194277`) : la normalisation des chemins en
absolu, et l'export JSON atomique. Son architecture de score de première transition
(`TransitionScore.leek`, `TransitionModel.leek`, ses modifications de BFS) **n'est pas
importée** : c'est une hypothèse de scoring à tester séparément, pas un composant du harnais.
Cette branche est conservée, rien n'y est supprimé.

Remplacés, et pourquoi : `report_is_current()` vérifiait le mode, le nombre de paires, les
tours et le fichier de vecteur, mais pas l'intégralité du code. Une modification de
`Scoring.leek` ne l'invalidait pas. La clé de match porte désormais les bundles complets de
toutes les politiques, le scénario résolu, le moteur, le runner et la version du parseur.

Abandonnés de l'ancien protocole : le plancher de cœurs, les combats tronqués à trois tours,
les seuils de coût ×2/×3, et la fitness fondée sur la vie restante. Le critère est l'issue
officielle du moteur ; la vie restante ne sert qu'au diagnostic.

## Décisions structurantes

**Aucun audit de coupe, aucun veto sur le coût en opérations.** Le harnais sélectionne la
performance de l'ensemble scoring plus élagage sous budget réel. Conséquence assumée : il ne
certifie pas l'admissibilité mathématique de la borne, et il ne distinguera pas toujours une
mauvaise idée de scoring d'une coupe qui la pénalise.

**Cœurs réels dès la sélection.** Un scoring plus cher perd de la recherche, et c'est le
combat qui doit le facturer.

**La team est synthétique.** Deux éleveurs de deux poireaux par camp, composés depuis le
snapshot du méta. Aucune donnée de la team réellement jouée n'existe ici, et le format le
déclare. Ne pas présenter ses résultats comme représentatifs de la vraie team.

**Les dix politiques de la ligue sont un ensemble de validation, pas un test éternel.** Des
graines neuves limitent la spécialisation aux scénarios, pas aux adversaires eux-mêmes. Dès
qu'un audit informe une retouche, il participe à la sélection.

# Validation indépendante du scoring Astra

Depuis la racine du dépôt, Python 3.11+ standard et le JDK correspondant au générateur (Java 25 utilisé ici). Aucun paquet Python supplémentaire. Le générateur doit déjà être compilé et contenir `generator.jar`, `leekscript/leekscript.jar` et `data/`.

## Reproduction

Choisir un répertoire de travail **distinct du générateur source et hors de `New_AI`**. Les commandes ci-dessous utilisent `../astra-runtime`. Les données et JARs y sont copiés sans modification. Les IA doivent rester sous cette racine de travail pour le résolveur natif du moteur.

```text
python -B validation/scoring_astra/runtime.py --engine CHEMIN_GENERATEUR --runtime ../astra-runtime
python -B -m unittest discover -s validation/scoring_astra -p test_oracle.py -v
python -B validation/scoring_astra/properties.py --runtime ../astra-runtime
python -B validation/scoring_astra/integration.py --runtime ../astra-runtime
python -B validation/scoring_astra/cost.py --runtime ../astra-runtime
python -B validation/scoring_astra/native_oracle.py --runtime ../astra-runtime --seeds 32
python -B validation/scoring_astra/review_rollouts.py --runtime ../astra-runtime
python -B validation/scoring_astra/real_smoke.py --runtime ../astra-runtime --builds CHEMIN_BUILDS_V3_JSONL
python -B validation/scoring_astra/provenance.py --engine CHEMIN_GENERATEUR --runtime ../astra-runtime
```

Le fichier privé de builds est celui de la campagne existante, `training/data/builds_v3.jsonl`. Il n'est pas redistribué ici ; son empreinte figure dans `results/smoke.json`. Ne pas reconstruire des builds « proches » en affirmant reproduire les mêmes combats.

La baseline `3024424` doit exister dans l'historique Git pour `cost.py` et `real_smoke.py`. Ces commandes ne modifient ni branche, ni registre, ni champion et ne lancent aucune boucle. Les sondes sont ajoutées uniquement aux copies privées des bundles.

## Fichiers et interprétation

- `PropertiesProbe.leek` : propriétés des fonctions du score et cohérence aux seuils.
- `IntegrationProbe.leek` : conséquences réelles, chaînes, score incrémental, résurrection, placement et majorant ; les cœurs de ces scénarios de diagnostic sont augmentés, sans changer un build sauvegardé.
- `RolloutProbe.leek` : alternatives et politique de continuation imposées ; le scoring est celui de la branche, compilé par LeekScript.
- `OracleRunner.java` : écoute passive et lecture de l'état terminal effectif. Le moteur lui-même reste inchangé.
- `native_oracle.py` : 120 situations, 2 options, graines appariées. Par défaut 8 graines pour une passe rapide ; le résultat livré utilise 32. Un mauvais cast ou une erreur invalide la paire. L'état de départ doit être identique.
- `review_rollouts.py` : nombres de victoires/défaites, PV et contributions brut/placement pour chaque désaccord. À lancer immédiatement après l'oracle, sur les mêmes sorties brutes.
- `real_smoke.py` : 12 combats complets au maximum de 64 tours, avec les cœurs réels, en solo, éleveur et team. Les scores de victoire de ce minuscule lot sont descriptifs.
- `cost.py` : mesure d'opérations à état identique, séparée de la divergence des trajectoires.

Dans `results/`, les cas, 7 680 observations compactes, rapports, contre-exemples et empreintes sont versionnés. Les journaux natifs complets et scénarios générés restent dans le runtime privé pour ne pas ajouter des centaines de Mo au dépôt. Le préfixe du bundle indique le test ; son suffixe identifie les fichiers `.leek` de production avant ajout de la sonde. Il dépend des octets, donc des fins de ligne. `provenance.json` contient également les empreintes de sources normalisées en LF pour comparer les checkouts.

La concordance avec l'oracle concerne **une continuation précise sur 4 à 6 tours**, pas le meilleur jeu possible. Les 16 désaccords sont expliqués et laissés visibles dans le [rapport](../../docs/scoring-astra.md). Le banc ne contient pas de test natif BR ni de comparaison contre les joueurs du top.

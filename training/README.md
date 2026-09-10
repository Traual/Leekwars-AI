# Harnais d'amélioration du scoring

Laboratoire local : proposer un changement de scoring, le tester par étapes, garder les
résultats, et promouvoir un champion seulement quand une règle figée d'avance le permet.

**Aucune campagne autonome n'est lancée.** `run-loop` crible sous trois budgets obligatoires ;
la confirmation et la promotion restent des commandes explicites.

## Commandes

```bash
python training/cli.py doctor
python training/cli.py init-campaign
python training/cli.py calibrate --time-budget 600 --formats solo,farmer --par-format 8 --workers 2
python training/cli.py register-candidate --id cand-001 --patch chemin/vers.patch --hypothese "..."
python training/cli.py evaluate --id cand-001 --stage s1 --blocs '{"farmer":2,"solo":1}'
python training/cli.py evaluate --id cand-001 --stage confirm --promouvoir
python training/cli.py report --id cand-001
python training/cli.py audit-br --id cand-001 --lobbies 12
python training/cli.py resume
```

Première campagne bornée, la commande complète :

```bash
python training/cli.py init-campaign && python training/cli.py run-loop \
  --patches training/runs/patches --max-candidats 4 --max-confirmations 1 \
  --budget-minutes 60 --workers 2
```

## Ce que fait chaque étage

**Le bloc** est l'unité statistique : quatre combats contre le même adversaire, mêmes builds
attachés aux mêmes créneaux, seule l'affectation des politiques change. `d = x(C) − x(H)`, un
par bloc. Les deux miroirs d'une même graine sont corrélés et ne font jamais deux observations.

**Le cache** porte les bundles complets de toutes les politiques, le scénario résolu, le
moteur, le runner et la version du parseur. Les deux combats champion contre adversaire n'ont
pas le bundle du candidat dans leur clé : ils se partagent entre tous les candidats évalués sur
le bloc. Mesuré sur un parcours réel : le premier candidat coûte 12 combats, le second n'en
ajoute que 6, et le temps passe de 63 à 29 secondes.

**La confirmation** tire des graines neuves après le gel du candidat, à taille figée, une seule
décision. Les bornes sont des approximations de Student avec des degrés de liberté de
Welch–Satterthwaite. Une variance nulle ou un effectif sous deux blocs donne NON CONCLUSIF, pas
une certitude : des résultats identiques ne prouvent pas l'équivalence de deux politiques.

**La promotion** vérifie que le champion attendu est toujours courant, écrit le manifeste, le
commit et le tag annoté, puis contrôle que l'arbre `New_AI` du commit est exactement le bundle
mesuré. Un écart annule la promotion.

**La reprise** lit l'état réel de Git plutôt que de croire le journal SQLite, parce que les deux
systèmes ne partagent pas de transaction. Chaque étape est idempotente, et une publication
interrompue est terminée ou constatée, jamais refaite.

**L'audit BR** remplace une politique focale dans un lobby autrement figé, C puis H. Aucun veto :
c'est un rapport. Le rang normalisé demande le classement officiel du moteur, que la sortie du
runner ne porte pas encore ; seule la fréquence de victoire est rapportée, plutôt qu'un rang
reconstruit sans validation.

## Débit mesuré

Cœurs réels des builds, combats complets jusqu'à la limite normale de 64 tours, huit combats
par format.

| workers | format | exécution médiane | p90 | combats/minute |
|---:|---|---:|---:|---:|
| 1 | solo | 2,16 s | 4,00 s | 24,1 |
| 1 | farmer | 6,15 s | 12,53 s | 7,5 |
| 2 | solo | 3,01 s | 5,13 s | 28,9 |
| 2 | farmer | 8,28 s | 15,20 s | 9,4 |

Deux workers rendent environ **+23 %** de débit, pas le double : les JVM se disputent la
machine, et le temps par combat monte. Les résultats, eux, sont **identiques** aux exécutions
séquentielles, vérifié sur huit combats, vainqueur et durée compris. C'est ce test qui autorise
le parallélisme, pas l'espoir.

## Deux pièges devenus des tests

**Un bundle hors de la racine du générateur ne se charge pas.** Le `NativeFileSystem` du
compilateur résout depuis sa propre racine. Le combat se lance quand même, l'IA lève à chaque
tour, et le combat se termine en une fraction de seconde parce qu'il n'a rien calculé : 128
erreurs par combat pris pour un débit exceptionnel. Nuance apprise en écrivant le test : ce
n'est pas l'absolu qui casse, un chemin absolu sous la racine fonctionne. La mesure de débit ne
compte donc que les combats valides, et `test_un_combat_sans_ia_ne_compte_pas` le vérifie.

**Le nom du répertoire d'un bundle est son empreinte.** Le générateur ressert un binaire compilé
quand un nom a déjà servi. Nommer par l'empreinte rend ce comportement correct : un nom
identique signifie un contenu identique.

## Tests

```bash
python training/tests/test_harnais.py       # 22 tests, instantanés
python training/tests/test_publication.py   #  3 tests, dépôt git temporaire
python training/tests/test_reels.py         #  2 tests, joue de vrais combats
```

Les tests de publication travaillent dans un dépôt jetable avec des résultats **synthétiques
explicitement étiquetés**, et un garde-fou vérifie qu'aucun d'eux n'a promu quoi que ce soit
dans le vrai registre.

## Ce qui vient de l'ancien harnais

Repris de `agent/training-harness-meta-builds` (`f7804cf`) : `tools/BatchRunner.java` pour sa
JVM réutilisée et son injection explicite de `fight_type` / `fight_context`, que
`Scenario.fromFile` ne désérialise pas ; le snapshot de builds ; `RESULTS-2026-08.md` comme
trace datée, dont les scores **ne sont pas** des mesures du champion actuel. De
`agent/hybrid-transition-scoring` (`7194277`) : la normalisation des chemins et l'export
atomique. Son architecture de score de première transition n'est pas importée, et sa branche
n'est pas touchée.

Remplacé : l'ancien contrôle de fraîcheur regardait le mode, le nombre de paires, les tours et
le fichier de vecteur. Une modification de `Scoring.leek` ne l'invalidait pas.

Abandonné : le plancher de cœurs, les combats tronqués, les seuils de coût, et la fitness fondée
sur la vie restante. Le critère est l'issue officielle du moteur.

## Décisions structurantes, et ce qu'elles coûtent

**Aucun audit de coupe, aucun veto sur le coût en opérations.** Le harnais sélectionne la
performance de l'ensemble scoring plus élagage sous budget réel. Il ne certifie pas
l'admissibilité de la borne, et il ne distinguera pas toujours une mauvaise idée de scoring
d'une coupe qui la pénalise.

**La team est synthétique.** Deux éleveurs de deux poireaux par camp, composés depuis le
snapshot. Aucune donnée de la team réellement jouée n'existe ici.

**Les empreintes de répertoire et de commit vivent dans des domaines séparés.** Un même code
matérialisé sur disque et lu depuis Git ne donne pas la même empreinte, les fins de ligne
différant. La détection de doublons ne rapproche donc pas une politique de la ligue de l'ancre
historique, même si leur code est identique.

**Les dix politiques sont un ensemble de validation, pas un test éternel.** Des graines neuves
limitent la spécialisation aux scénarios, pas aux adversaires eux-mêmes.

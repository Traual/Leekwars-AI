# Harnais d'amélioration du scoring

Laboratoire local : proposer un changement de scoring, le tester par étapes, garder les
résultats, et promouvoir un champion seulement quand une règle figée d'avance le permet.

**Aucune campagne autonome n'est lancée ici.** `run-loop` parcourt en revanche la boucle
entière — proposition, S1, sélection, S2, S3, confirmation, promotion — sous trois budgets
obligatoires. Ce qui reste interdit n'est pas la transition, c'est de promouvoir sur un
protocole incomplet ou non figé : le décideur rend alors INCOMPLET et la publication n'a pas
lieu.

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

`run-loop` parcourt alors proposition → S1 → sélection → S2 → S3 → confirmation → promotion.
Les trois budgets sont obligatoires et réellement décomptés : l'échéance descend jusqu'au
processus de combat, qui coupe son lot en gardant les combats déjà terminés. Une publication
laissée en vol est reprise avant toute autre chose.

`config/reception.yaml` est un protocole de RECEPTION, pas de décision : tailles minuscules et
risque non contrôlé, pour exercer la machinerie de confirmation sur de vrais combats en un
temps borné. Aucune promotion ne doit en sortir.

## Ce que fait chaque étage

**Le bloc** est l'unité statistique : quatre combats contre le même adversaire, mêmes builds
attachés aux mêmes créneaux, seule l'affectation des politiques change. `d = x(C) − x(H)`, un
par bloc. Les deux miroirs d'une même graine sont corrélés et ne font jamais deux observations.

**Le cache** porte les bundles complets de toutes les politiques, le scénario résolu, le
moteur, le runner et la version du parseur. Les deux combats champion contre adversaire n'ont
pas le bundle du candidat dans leur clé : ils se partagent entre tous les candidats évalués sur
le bloc. Mesuré sur un parcours réel : le premier candidat coûte 12 combats, le second n'en
ajoute que 6, et le temps passe de 63 à 29 secondes.

**La confirmation** est une TENTATIVE nommée, enregistrée après le gel du candidat : elle fige
son plan de graines, le champion comparé, ses tailles et l'empreinte du protocole. Reprendre une
tentative rejoue exactement le même échantillon ; en ouvrir une nouvelle tire une vague de
graines inédite, et le budget de campagne les décompte. Retoucher une idée après avoir vu ses
résultats puis la reconfirmer porte donc bien sur un échantillon neuf. Taille figée, une seule
décision. Les bornes sont des approximations de Student avec des degrés de liberté de
Welch–Satterthwaite. Une variance nulle ou un effectif sous deux blocs donne NON CONCLUSIF, pas
une certitude : des résultats identiques ne prouvent pas l'équivalence de deux politiques.

**La décision** porte sur la COUVERTURE, pas sur l'échantillon survivant. Tous les blocs
prévus, tous les adversaires prévus et tous les formats requis doivent être présents ; une
lacune donne INCOMPLET. Un lot aux tailles imposées à la main rend au mieux INDICATIF, jamais
PROMOUVOIR.

**La promotion** vérifie que le champion attendu est toujours actif, prépare le code et le
manifeste, contrôle que l'arbre préparé est exactement le bundle mesuré **avant** de commiter,
pose le tag annoté, et n'écrit le pointeur du champion actif qu'en dernier. Un écart de bundle
n'ajoute donc rien à l'histoire.

**La reprise** lit l'état réel de Git plutôt que de croire le journal SQLite, parce que les deux
systèmes ne partagent pas de transaction. Deux issues, jamais d'état intermédiaire : la
publication est terminée, ou elle est abandonnée — pointeur restauré, arbre de travail nettoyé,
manifeste orphelin retiré. Tant qu'une publication n'est pas close, `champion_courant` rend le
champion précédent, conservé dans la ligne de publication.

**L'audit BR** remplace une politique focale dans un lobby autrement figé, C puis H, sur la
MÊME graine : la composition et le tirage sont faits une fois pour les deux. Une paire dont un
côté manque reste incomplète et n'alimente aucune fréquence — une panne n'est pas une défaite.
Aucun veto :
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

## Trois pièges devenus des tests

**Un bundle hors de la racine du générateur ne se charge pas.** Le `NativeFileSystem` du
compilateur résout depuis sa propre racine et refuse tout ce qui en sort. Le combat se lance
quand même, l'IA lève à chaque tour, et il se termine en une fraction de seconde parce qu'il n'a
rien calculé : 128 erreurs par combat pris pour un débit exceptionnel. Nuance apprise en
écrivant le test : ce n'est pas l'absolu qui casse, un chemin absolu sous la racine fonctionne.
Le runner remonte désormais les erreurs SYSTÈME du moteur, et c'est ce diagnostic — pas le coût
ni l'issue — qui écarte un combat d'une mesure de débit. Un combat lent, perdu, ou dont l'IA
épuise son plafond d'opérations compte pleinement.

**Un camp est une sous-liste de `entities`, et rien d'autre.** Le générateur incrémente son
compteur de camp à chaque sous-liste ; le champ JSON `team` ne sert qu'à nommer l'équipe. Une
team écrite en quatre sous-listes de deux poireaux donnait quatre camps, et les deux éleveurs
censés coopérer se battaient entre eux. Le contrôle porte maintenant sur l'état moteur.

**Le nom du répertoire d'un bundle est son empreinte.** Le générateur ressert un binaire compilé
quand un nom a déjà servi. Nommer par l'empreinte rend ce comportement correct : un nom
identique signifie un contenu identique.

## Tests

```bash
python training/tests/test_harnais.py       # 32 tests, instantanés
python training/tests/test_boucle.py        #  9 tests, moteur synthétique, ~20 s
python training/tests/test_publication.py   #  5 tests, dépôt git temporaire
python training/tests/test_reels.py         #  3 tests, joue de vrais combats
```

`test_boucle.py` appelle `cmd_run_loop`, `cmd_evaluate` et `evaluer_etape` tels que les
commandes les appellent ; seul le processus de combat est remplacé par un moteur synthétique qui
lit la « force » déclarée par chaque bundle. Le plan de blocs, le cache, les workers, les points
de reprise, la couverture, la décision, les tentatives de confirmation et la publication Git
sont le code de production.

Les tests de publication et de boucle travaillent dans un dépôt jetable avec des résultats
**synthétiques explicitement étiquetés**, et un garde-fou vérifie qu'aucun d'eux n'a promu quoi
que ce soit dans le vrai registre.

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

**La team est synthétique.** Deux camps réels de quatre poireaux, deux éleveurs par camp,
composés depuis le snapshot. Aucune donnée de la team réellement jouée n'existe ici.

**Les empreintes de répertoire et de commit vivent dans des domaines séparés.** Un même code
matérialisé sur disque et lu depuis Git ne donne pas la même empreinte, les fins de ligne
différant. La détection de doublons ne rapproche donc pas une politique de la ligue de l'ancre
historique, même si leur code est identique.

**Les dix politiques sont un ensemble de validation, pas un test éternel.** Des graines neuves
limitent la spécialisation aux scénarios, pas aux adversaires eux-mêmes.

**Le protocole est figé par campagne.** Moteur, runner, parseur, builds, panel de ligue et
configuration entrent dans une empreinte vérifiée avant chaque évaluation. En changer un seul
demande une NOUVELLE campagne : `init-campaign` refuse de réécrire une campagne existante sous
un autre protocole, parce que des résultats déjà lus ne doivent pas changer de sens en silence.

**Le périmètre de l'optimiseur est bloquant.** Une extension non résolue ne participe ni au
crible, ni à la confirmation, ni à la promotion. Autoriser `BFS.leek` autorise la coupe et son
majorant, pas le retrait des gardes du budget interne d'opérations : cet invariant est vérifié
sur le code du candidat. Un helper nécessaire peut être autorisé explicitement, et cette
autorisation est journalisée.

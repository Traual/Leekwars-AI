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

**La boucle se relance.** Un candidat déjà enregistré est repris, pas réinscrit : même
identifiant, même branche, même commit, et son avancement revient du registre. Un commit de
candidat créé avant une panne de stockage est même reconstruit depuis Git, après vérification
de son parent et de l'empreinte de sa proposition, inscrite dans son message de commit. Les
étapes déjà franchies ne sont pas rejouées, et une confirmation coupée reprend sa tentative —
même plan de graines, même champion comparé, sans consommer une unité de budget de plus. Seuls
les combats manquants sont joués. Chaque étape garde son rapport complet dans `runs/rapports`,
et chaque lancement son journal dans `runs/boucles`.

**La décision de confirmation et sa promotion forment une seule opération récupérable.** La
décision complète est écrite avec sa tentative, dans la transaction qui la clôt, avec
l'intention de publication qui en découle. Une décision positive dont la promotion n'a pas
abouti est terminée à la relance, après contrôle du candidat, du protocole et du champion
attendu — jamais rejouée, jamais oubliée. `evaluate --stage confirm` suit le même chemin :
avec `--promouvoir` il termine la décision en attente, et il faut `--nouvelle-tentative` pour
demander explicitement un nouvel échantillon. Une publication interrompue pour raison technique
laisse la décision publiable ; un bundle incohérent la rend caduque, et le journal distingue
les deux.

**Les budgets se vérifient avant de demander une proposition, et à son retour.** Un plafond
atteint ou un temps épuisé ne produit ni appel à l'optimiseur, ni commit, ni inscription.
L'échéance restante voyage avec la demande, pour qu'un fournisseur externe puisse la respecter,
et elle est reconstatée quand il répond : un patch arrivé trop tard est conservé comme
proposition à reprendre, sans qu'aucune opération Git ne commence.

**Une promotion refuse de détruire un travail local.** La préparation vide `New_AI` pour y
poser exactement l'arbre du candidat, et la liste des fichiers étrangers ne mémorise que des
chemins, jamais des contenus. Un arbre propre à l'inscription du candidat ne l'est pas
forcément des heures plus tard, à la promotion : la zone est donc reconstatée juste avant, et
l'opération s'arrête sans rien toucher si elle porte des modifications non commitées. La
décision reste publiable une fois la zone libérée.

**Une branche de candidat ne se reprend que sur preuve.** Le commit porte l'empreinte de sa
source dans son message. S'il n'en porte aucune — branche ancienne, ou créée autrement — la
reprise exige que le patch demandé reconstruise exactement son arbre, vérifié dans un index
temporaire. Sans preuve, elle refuse au lieu de recopier l'empreinte entrante.

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

**Le plan doit donc être atteignable**, et `init-campaign` le refuse sinon. Un bloc porte un
seul adversaire : chaque format actif a besoin d'au moins autant de blocs que l'étape a
d'adversaires. Le contrôle vérifie aussi l'emboîtement sur les tailles retenues — les blocs par
adversaire ne doivent jamais décroître d'une étape à la suivante, sans quoi S1 ⊄ S2 et le coût
annoncé entre étapes n'est pas cumulatif.

**La promotion** vérifie que le champion attendu est toujours actif, prépare le code et le
manifeste, contrôle que l'arbre préparé est exactement le bundle mesuré **avant** de commiter,
pose le tag annoté, et n'écrit le pointeur du champion actif qu'en dernier. Un écart de bundle
n'ajoute donc rien à l'histoire.

**La reprise** lit l'état réel de Git plutôt que de croire le journal SQLite, parce que les deux
systèmes ne partagent pas de transaction — et parce qu'une coupure tombe aussi entre une
écriture Git réussie et la ligne SQLite qui la note. Elle cherche donc le commit de champion par
trois faits successifs, le tag, le SHA enregistré, puis le manifeste versionné sur la branche,
et elle lit le pointeur tel qu'il est **commité**, pas tel qu'il traîne dans l'arbre de travail.
Deux issues, jamais d'état intermédiaire : la publication est terminée et contrôlée — tag,
manifeste versionné, pointeur commité, arbre propre — ou elle est abandonnée, pointeur restauré
et zone de publication remise en état. Le nettoyage retire ce que la publication a **créé**, y
compris les fichiers qu'un candidat ajoute, et préserve ce qui traînait déjà ; la propreté est
ensuite constatée, pas supposée. Tant qu'une publication n'est pas close,
`champion_courant` rend le champion précédent, conservé dans la ligne de publication.

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

## Quatre pièges devenus des tests

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

**Préparer un sous-arbre depuis un commit est une SUPERPOSITION.** `git checkout <commit> --
New_AI` laisse en place les fichiers suivis absents du candidat. Un candidat qui retire un
helper de scoring voyait donc ce helper survivre dans l'arbre publié, l'empreinte différer de
celle qui avait été mesurée, et sa promotion refusée — alors que son bundle était correct. On
vide l'index et le disque avant de reposer le sous-arbre.

**Le nom du répertoire d'un bundle est son empreinte.** Le générateur ressert un binaire compilé
quand un nom a déjà servi. Nommer par l'empreinte rend ce comportement correct : un nom
identique signifie un contenu identique.

## Tests

```bash
python training/tests/test_harnais.py       # 32 tests, instantanés
python training/tests/test_boucle.py        # 20 tests, moteur synthétique, ~110 s
python training/tests/test_publication.py   #  7 tests, dépôt git temporaire
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

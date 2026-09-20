"""Diagnostic cible d'une decision du scoring simple.

Trois sous-commandes, un repertoire de travail, des fichiers JSON :

  combats     joue des combats eleveur aux coeurs reels contre une reference (le champion par
              defaut), archive le journal d'actions et les journaux des IA de chacun, et
              ARCHIVE LE BUNDLE joue. Sans cette archive, un rejeu ne peut pas certifier qu'il
              diagnostique le meme code que le combat d'origine.
  situations  classe les tours de nos poireaux par ce qu'ils ont PAYE pour ce qu'ils ont
              obtenu. Une defaite n'est pas une erreur ; cette liste ne fait que proposer ou
              regarder.
  decision    rejoue UN combat DEPUIS SON BUNDLE ARCHIVE, diagnostic arme sur (tour, entite),
              verifie la trajectoire, et rend le detail de chaque replanification du tour.

Le diagnostic est eteint dans le depot (DIAG_TURN = 0) : seule la copie deployee par la
sous-commande `decision` l'arme.

Usage :
  python decision.py combats    <travail> [--graine N] [--combats N] [--reference <commit>]
  python decision.py situations <travail> [--combien N] [--invocations]
  python decision.py decision   <travail> --combat <etiquette> --tour T --entite E [--sans-item ID]
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ICI = Path(__file__).resolve().parent
TRAINING = ICI.parent
DEPOT = TRAINING.parent
sys.path.insert(0, str(TRAINING))
sys.path.insert(0, str(TRAINING / "runs" / "v3" / "outils"))

import bundle as mod_bundle      # noqa: E402
import scenarios as mod_sc       # noqa: E402
from fumee300 import GEN, BUILD  # noqa: E402

CHAMPION = "c8ce20e"
# Types d'actions du moteur (generator/action/Action.java) utiles ici.
A_MORT, A_NEW_TURN, A_LEEK_TURN = 5, 6, 7
A_LOST_LIFE, A_DAMAGE_RETURN, A_LIFE_DAMAGE, A_POISON, A_AFTEREFFECT = 101, 108, 109, 110, 111
A_ERREUR_IA = 1002
DEGATS = (A_LOST_LIFE, A_DAMAGE_RETURN, A_LIFE_DAMAGE, A_POISON, A_AFTEREFFECT)


# ------------------------------------------------------------------ deploiement et execution

def empreinte(racine: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(racine.rglob("*.leek")):
        h.update(str(f.relative_to(racine)).encode() + b"\0" + f.read_bytes())
    return h.hexdigest()[:10]


def deployer_source(source: Path, marque: str, tour: int = 0, entite: int = -1,
                    sans_item: int = -1) -> str:
    """Copie un arbre d'IA sous un nom NEUF — le generateur recompile par date de repertoire et
    resservirait sinon un binaire perime — et y arme le diagnostic si un tour est demande."""
    nom = "%s-%s-%d" % (marque, empreinte(source), int(time.time() * 1000) % 10 ** 9)
    dest = GEN / "test" / "ai" / "bundles" / nom
    shutil.copytree(source, dest)
    if tour:
        armer(dest, tour, entite, sans_item)
    maintenant = time.time()
    for f in dest.rglob("*"):
        if f.is_file():
            os.utime(f, (maintenant, maintenant))
    return "test/ai/bundles/%s/Main.leek" % nom


def deployer_commit(ref: str) -> str:
    emp = mod_bundle.empreinte(ref)
    nom = "ref-%s-%d" % (emp["sha256"][:10], int(time.time() * 1000) % 10 ** 9)
    dest = GEN / "test" / "ai" / "bundles" / nom
    mod_bundle.materialiser(ref, dest)
    maintenant = time.time()
    for f in dest.rglob("*"):
        if f.is_file():
            os.utime(f, (maintenant, maintenant))
    return "test/ai/bundles/%s/Main.leek" % nom


def armer(dest: Path, tour: int, entite: int, sans_item: int) -> None:
    """Arme le diagnostic dans la COPIE deployee : trois constantes, rien d'autre."""
    fichier = dest / "Scoring" / "Scoring.leek"
    texte = fichier.read_text(encoding="utf-8")
    for ancre, valeur in (("global DIAG_TURN = 0", tour),
                          ("global DIAG_ENTITY = -1", entite),
                          ("global DIAG_WITHOUT_ITEM = -1", sans_item)):
        assert texte.count(ancre) == 1, "ancre absente : %s" % ancre
        texte = texte.replace(ancre, ancre.rsplit(" ", 1)[0] + " " + str(valeur))
    fichier.write_text(texte, encoding="utf-8")


def jouer(chemins: list[Path], timeout: int = 7200) -> list[dict]:
    """Joue les scenarios et rend la sortie COMPLETE du moteur : actions, journaux, vainqueur."""
    cp = os.pathsep.join([str(TRAINING / "runs" / "v3" / ".build-fid"), str(GEN / "generator.jar"),
                          str(GEN / "leekscript" / "leekscript.jar")])
    # Sans forcer l'encodage, la JVM ecrit les journaux dans la page de code Windows et tous les
    # accents des lignes de diagnostic reviennent casses.
    env = dict(os.environ, JAVA_TOOL_OPTIONS="-Dtraual.profile=true -Dfile.encoding=UTF-8"
                                             " -Dsun.stdout.encoding=UTF-8 -Dstdout.encoding=UTF-8")
    proc = subprocess.run(["java", "-cp", cp, "training.fidelite.FideliteRunner"]
                          + [str(p) for p in chemins],
                          cwd=str(GEN), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env)
    par_indice: dict[int, dict] = {}
    for ligne in proc.stdout.splitlines():
        if ligne.startswith("__FIDELITE__\t"):
            _, indice, charge = ligne.split("\t", 2)
            par_indice[int(indice)] = json.loads(charge)
    if len(par_indice) != len(chemins):
        print(proc.stdout[-2000:])
        print(proc.stderr[-2000:])
        raise SystemExit("le moteur n'a pas rendu %d combats" % len(chemins))
    return [par_indice[i] for i in range(len(chemins))]


# ------------------------------------------------------------------ lecture d'un combat

def actions(brut: dict) -> list:
    return ((brut.get("outcome") or {}).get("fight") or {}).get("actions") or []


def entites(brut: dict) -> dict[int, dict]:
    return {e["id"]: e for e in (((brut.get("outcome") or {}).get("fight") or {}).get("leeks") or [])}


def journaux(brut: dict) -> list[tuple[int, int, str]]:
    """Lignes de debug : [(indice d'action, entite JOURNALISEE, texte)], dans l'ordre du moteur.

    L'entite journalisee est celle de la VM : un bulbe ecrit sous son invocateur. L'entite qui
    JOUE se lit sur la fenetre de tour qui contient l'indice d'action, jamais sur ce champ.
    """
    lignes = []
    for _vm, par_indice in ((brut.get("outcome") or {}).get("logs") or {}).items():
        for indice, journal in par_indice.items():
            for rang, l in enumerate(journal):
                if len(l) >= 3 and isinstance(l[2], str):
                    lignes.append((int(indice), rang, l[0], l[2]))
    # Par indice d'action, puis dans l'ORDRE D'EMISSION du journal. Trier sur le texte
    # melangerait les lignes d'une meme decision par ordre alphabetique.
    lignes.sort(key=lambda x: (x[0], x[1]))
    return [(i, vm, t) for i, _r, vm, t in lignes]


def fenetres(brut: dict) -> list[dict]:
    """Une fenetre par tour d'entite : numero de tour, entite ACTIVE, [debut, fin[ en indices."""
    actes = actions(brut)
    sortie, tour = [], 1
    for i, act in enumerate(actes):
        if not act:
            continue
        if act[0] == A_NEW_TURN:
            # Le moteur n'emet pas de NEW_TURN pour le PREMIER tour : il commence a 1 d'office.
            tour = act[1] if len(act) > 1 else tour + 1
        elif act[0] == A_LEEK_TURN and len(act) > 1:
            if sortie:
                sortie[-1]["fin"] = i
            sortie.append({"tour": tour, "entite": act[1], "debut": i, "fin": len(actes)})
    return sortie


def fenetre_visee(brut: dict, tour: int, entite: int) -> dict | None:
    for f in fenetres(brut):
        if f["tour"] == tour and f["entite"] == entite:
            return f
    return None


def table_templates() -> tuple[dict[int, tuple[str, int]], dict[int, tuple[str, int]]]:
    """[template -> (nom, id d'item de l'IA)] pour les puces et pour les armes, SEPAREMENT.

    Le journal du moteur enregistre le TEMPLATE, pas l'identifiant d'item, et les deux espaces
    se recouvrent : le template 34 est la puce `adrenaline` ET l'arme `unstable_destroyer`. Seul
    le TYPE de l'action tranche. L'identifiant d'item de l'IA est `id` pour une puce et `item`
    pour une arme (l'arme `sword` a l'id moteur 35 et l'item 277).
    """
    donnees = GEN / "data"
    puces = {}
    for v in json.loads((donnees / "chips.json").read_text(encoding="utf-8")).values():
        puces[v["template"]] = (v["name"], v["id"])
    armes = {}
    for v in json.loads((donnees / "weapons.json").read_text(encoding="utf-8")).values():
        armes[v["template"]] = (v["name"], v.get("item", v["id"]))
    return puces, armes


def actions_jouees(brut: dict, tour: int, entite: int) -> list[dict]:
    """Ce que l'entite a REELLEMENT joue pendant ce tour, lu dans le journal du moteur.

    Source de verite : `fight.actions`. Les lignes de debug « Using : » n'en couvrent qu'une
    partie — ni l'invocation ni le saut n'en produisent, et elles s'arretent avant la fin du
    tour — s'y fier fait disparaitre des casts.

    Payloads du moteur : USE_CHIP [12, template, cellule, succes], SET_WEAPON [13, template],
    USE_WEAPON [16, cellule, succes] (l'arme vient du dernier SET_WEAPON), MOVE_TO
    [10, entite, cellule, chemin], SUMMON [9, invocateur, invoque, cellule, resultat].
    """
    puces, armes = table_templates()
    fen = fenetre_visee(brut, tour, entite)
    if fen is None:
        return []

    def arme_nommee(template):
        if template is None:
            return ("arme inconnue", None)
        return armes.get(template, ("template %d inconnu" % template, None))

    # UN SEUL balayage CHRONOLOGIQUE depuis le debut du combat. L'arme equipee est posee par
    # SET_WEAPON, qui ne porte pas d'entite — elle appartient a celle dont c'est le tour — et
    # elle persiste d'un tour a l'autre. Chaque USE_WEAPON est donc decode avec l'arme equipee
    # A CET INSTANT : reconstruire l'etat jusqu'a la fin du tour puis l'appliquer a tous les
    # tirs attribuait la DERNIERE arme du tour a ceux qui l'avaient precedee.
    arme_de: dict[int, int] = {}
    actif = None
    sortie = []
    for i, act in enumerate(actions(brut)):
        if i >= fen["fin"]:
            break
        if not act:
            continue
        dans = i >= fen["debut"]
        if act[0] == A_LEEK_TURN and len(act) > 1:
            actif = act[1]
        elif act[0] == 13 and len(act) > 1:
            if actif is not None:
                arme_de[actif] = act[1]
            if dans:
                nom, item = arme_nommee(act[1])
                sortie.append({"indice": i, "sorte": "arme equipee", "nom": nom, "item": item,
                               "cible": None})
        elif dans and act[0] == 12 and len(act) > 2:
            nom, item = puces.get(act[1], ("template %d inconnu" % act[1], None))
            sortie.append({"indice": i, "sorte": "puce", "nom": nom, "item": item, "cible": act[2]})
        elif dans and act[0] == 16 and len(act) > 1:
            nom, item = arme_nommee(arme_de.get(entite))
            sortie.append({"indice": i, "sorte": "arme", "nom": nom, "item": item, "cible": act[1]})
        elif dans and act[0] == 10 and len(act) > 2 and act[1] == entite:
            sortie.append({"indice": i, "sorte": "deplacement", "nom": "->%d" % act[2], "item": None,
                           "cible": act[2]})
        elif dans and act[0] == 9 and len(act) > 3 and act[1] == entite:
            sortie.append({"indice": i, "sorte": "invocation", "nom": "invoque #%d" % act[2],
                           "item": None, "cible": act[3]})
    return sortie


def cmd_joue(args) -> int:
    """Les actions REELLEMENT jouees d'un tour, depuis le combat NORMAL archive."""
    travail = Path(args.travail).resolve()
    index = json.loads((travail / "index.json").read_text(encoding="utf-8"))
    combat = next(c for c in index["combats"] if c["etiquette"] == args.combat)
    brut = json.loads((travail / combat["fichier"]).read_text(encoding="utf-8"))
    ents = entites(brut)
    fen = fenetre_visee(brut, args.tour, args.entite)
    if fen is None:
        print("CIBLE ABSENTE : l'entite %d ne joue pas au tour %d" % (args.entite, args.tour))
        return 2
    jouees = actions_jouees(brut, args.tour, args.entite)
    casts = [a for a in jouees if a["sorte"] in ("puce", "arme")]
    print("%s | %s tour %d | entite %d %s | fenetre [%d, %d)"
          % (travail.name, args.combat, args.tour, args.entite,
             ents.get(args.entite, {}).get("name"), fen["debut"], fen["fin"]))
    for a in jouees:
        print("   %4d  %-13s %s%s" % (a["indice"], a["sorte"], a["nom"],
                                      (" #%s" % a["item"]) if a["item"] else ""))
    print("   => %d casts joues : %s" % (len(casts), ", ".join(a["nom"] for a in casts)))
    return 0


def actions_par_tour(brut: dict) -> list[dict]:
    """Chaque tour d'entite avec ses actions et la vie retiree a chacun pendant ce tour."""
    actes = actions(brut)
    sortie = []
    for f in fenetres(brut):
        bloc = {"tour": f["tour"], "entite": f["entite"], "actions": actes[f["debut"] + 1:f["fin"]],
                "degats": collections.Counter(), "morts": []}
        for act in bloc["actions"]:
            if not act:
                continue
            if act[0] in DEGATS and len(act) >= 3:
                bloc["degats"][act[1]] += act[2]
            elif act[0] == A_MORT and len(act) >= 2:
                bloc["morts"].append(act[1])
        sortie.append(bloc)
    return sortie


# ------------------------------------------------------------------ sous-commande : combats

def cmd_combats(args) -> int:
    travail = Path(args.travail).resolve()
    travail.mkdir(parents=True, exist_ok=True)
    # Le bundle JOUE est archive : c'est lui, et pas l'arbre de travail, que le rejeu instrumente.
    archive = travail / "bundle"
    if archive.exists():
        shutil.rmtree(archive)
    shutil.copytree(DEPOT / "New_AI", archive)
    builds = mod_sc.charger_builds(TRAINING / "data" / "builds_v3.jsonl")
    ia_nous = deployer_source(archive, "nous")
    ia_eux = deployer_commit(args.reference)
    chemins, etiquettes = [], []
    for b in mod_sc.plan_de_blocs("farmer", ["x"], args.combats, args.graine, builds,
                                  vague="diagnostic"):
        for cote, (g, d) in (("gauche", (ia_nous, ia_eux)), ("droite", (ia_eux, ia_nous))):
            chemin = travail / ("scenario-%d-%s.json" % (b.indice, cote))
            mod_sc.ecrire(chemin, mod_sc.scenario(b, builds, g, d))
            chemins.append(chemin)
            etiquettes.append("%d-%s" % (b.indice, cote))
    t0 = time.time()
    sorties = jouer(chemins)
    index = {"graine": args.graine, "reference": args.reference, "nous": ia_nous, "eux": ia_eux,
             "empreinte_bundle": empreinte(archive), "combats": []}
    for etiquette, chemin, brut in zip(etiquettes, chemins, sorties):
        cible = travail / ("combat-%s.json" % etiquette)
        cible.write_text(json.dumps(brut), encoding="utf-8")
        source = json.loads(chemin.read_text(encoding="utf-8"))
        notre_camp = 1 if source["entities"][0][0]["ai"] == ia_nous else 2
        avortes = [a for a in actions(brut) if a and a[0] == A_ERREUR_IA]
        index["combats"].append({
            "etiquette": etiquette, "scenario": chemin.name, "fichier": cible.name,
            "notre_camp": notre_camp, "vainqueur": brut.get("winner"),
            "tours": (brut.get("outcome") or {}).get("duration"), "tours_avortes": len(avortes),
        })
        print("combat %-10s | camp %d | vainqueur %s | tours %s | tours avortes %d"
              % (etiquette, notre_camp, brut.get("winner"),
                 (brut.get("outcome") or {}).get("duration"), len(avortes)))
    (travail / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print("%d combats en %.0f s | bundle archive %s -> %s"
          % (len(sorties), time.time() - t0, index["empreinte_bundle"], travail))
    return 0


# ------------------------------------------------------------------ sous-commande : situations

def cmd_situations(args) -> int:
    travail = Path(args.travail).resolve()
    index = json.loads((travail / "index.json").read_text(encoding="utf-8"))
    lignes = []
    for combat in index["combats"]:
        brut = json.loads((travail / combat["fichier"]).read_text(encoding="utf-8"))
        ents = entites(brut)
        notre = {i for i, e in ents.items() if e.get("team") == combat["notre_camp"]}
        # Les invocations meurent par dizaines sans que ce soit une erreur : seuls les poireaux
        # sont classes, sauf demande contraire.
        vises = notre if args.invocations else {i for i in notre if not ents[i].get("summon")}
        tours = actions_par_tour(brut)
        for k, t in enumerate(tours):
            if t["entite"] not in vises:
                continue
            inflige = sum(v for cible, v in t["degats"].items() if cible not in notre)
            subi_pendant = sum(v for cible, v in t["degats"].items() if cible in notre)
            encaisse, mort = 0, False
            for suite in tours[k + 1:]:
                if suite["entite"] == t["entite"]:
                    break
                encaisse += suite["degats"].get(t["entite"], 0)
                if t["entite"] in suite["morts"]:
                    mort = True
            lignes.append({
                "combat": combat["etiquette"], "tour": t["tour"], "entite": t["entite"],
                "nom": ents.get(t["entite"], {}).get("name"),
                "inflige": inflige, "subi_pendant": subi_pendant, "encaisse_apres": encaisse,
                "mort": mort, "solde": inflige - subi_pendant - encaisse,
                "actions": len(t["actions"]),
            })
    lignes.sort(key=lambda l: (not l["mort"], l["solde"]))
    (travail / "situations.json").write_text(json.dumps(lignes, indent=1), encoding="utf-8")
    print("%-12s %5s %7s %8s %9s %10s %6s %s"
          % ("combat", "tour", "entite", "inflige", "subi", "encaisse", "mort", "solde"))
    for l in lignes[:args.combien]:
        print("%-12s %5d %7d %8d %9d %10d %6s %d"
              % (l["combat"], l["tour"], l["entite"], l["inflige"], l["subi_pendant"],
                 l["encaisse_apres"], "oui" if l["mort"] else "non", l["solde"]))
    print("%d tours classes -> %s" % (len(lignes), travail / "situations.json"))
    return 0


# ------------------------------------------------------------------ sous-commande : decision

def premier_ecart(a: list, b: list) -> str | None:
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return "action %d : %s contre %s" % (i, a[i], b[i])
    if len(a) != len(b):
        return "longueurs differentes : %d contre %d" % (len(a), len(b))
    return None


def cmd_decision(args) -> int:
    travail = Path(args.travail).resolve()
    index = json.loads((travail / "index.json").read_text(encoding="utf-8"))
    combat = next(c for c in index["combats"] if c["etiquette"] == args.combat)
    archive = travail / "bundle"
    if not archive.exists():
        print("BUNDLE D'ORIGINE ABSENT (%s) : ce lot a ete joue avant l'archivage, et l'arbre de"
              " travail ne prouve pas qu'il porte le meme code. Rejouer `combats`." % archive)
        return 2
    if empreinte(archive) != index.get("empreinte_bundle"):
        print("ARCHIVE ALTEREE : empreinte %s contre %s enregistree."
              % (empreinte(archive), index.get("empreinte_bundle")))
        return 2
    reference = json.loads((travail / combat["fichier"]).read_text(encoding="utf-8"))
    cible_ref = fenetre_visee(reference, args.tour, args.entite)
    if cible_ref is None:
        print("CIBLE ABSENTE du combat de reference : l'entite %d ne joue pas au tour %d."
              % (args.entite, args.tour))
        return 2

    ia_diag = deployer_source(archive, "diag", args.tour, args.entite, args.sans_item)
    source = json.loads((travail / combat["scenario"]).read_text(encoding="utf-8"))
    nous, remplaces = index["nous"], 0
    for groupe in source["entities"]:
        for e in groupe:
            if e["ai"] == nous:
                e["ai"] = ia_diag
                remplaces += 1
    assert remplaces > 0, "aucune entite ne portait notre IA"
    chemin = travail / ("rejeu-%s-t%d-e%d.json" % (args.combat, args.tour, args.entite))
    chemin.write_text(json.dumps(source), encoding="utf-8")
    brut = jouer([chemin])[0]

    # L'instrumentation coute des operations, et des operations changent ce qu'un tour a le temps
    # de faire. Deux controles, car le diagnostic parle a CHAQUE replanification du tour :
    #  - le PREFIXE valide la premiere decision du tour ;
    #  - le TOUR COMPLET valide toutes les suivantes, qui se jouent apres la premiere emission.
    cible_diag = fenetre_visee(brut, args.tour, args.entite)
    if cible_diag is None:
        print("CIBLE ABSENTE du rejeu : l'entite %d ne joue pas au tour %d — l'instrumentation a"
              " change la trajectoire avant la decision." % (args.entite, args.tour))
        return 2
    a, b = actions(reference), actions(brut)
    ecart_prefixe = premier_ecart(a[:cible_ref["debut"]], b[:cible_diag["debut"]])
    ecart_tour = premier_ecart(a[:cible_ref["fin"]], b[:cible_diag["fin"]])
    if ecart_prefixe is None:
        print("prefixe identique au combat normal (%d actions) : la PREMIERE decision du tour est"
              " validee" % cible_ref["debut"])
    else:
        print("DIVERGENCE avant le tour : %s" % ecart_prefixe)
    if ecart_tour is None:
        print("tour complet identique (indices %d a %d) : TOUTES les decisions du tour sont validees"
              % (cible_ref["debut"], cible_ref["fin"] - 1))
    else:
        print("DIVERGENCE dans le tour vise : %s" % ecart_tour)
        print("   -> seules les decisions anterieures a cet ecart decrivent le combat de reference")

    avortes = [x for x in b if x and x[0] == A_ERREUR_IA]
    lignes = [t for i, _vm, t in journaux(brut)
              if cible_diag["debut"] <= i < cible_diag["fin"] and t.startswith(("[diag]", "[score]"))]
    sortie = {"combat": args.combat, "tour": args.tour, "entite": args.entite,
              "bundle": index["empreinte_bundle"], "sans_item": args.sans_item,
              "prefixe_identique": ecart_prefixe is None, "tour_identique": ecart_tour is None,
              "ecart_prefixe": ecart_prefixe, "ecart_tour": ecart_tour,
              "actions_avant": cible_ref["debut"], "tours_avortes": len(avortes), "lignes": lignes}
    cible = travail / ("decision-%s-t%d-e%d.json" % (args.combat, args.tour, args.entite))
    cible.write_text(json.dumps(sortie, indent=1, ensure_ascii=False), encoding="utf-8")
    for l in lignes:
        print(l)
    print("-> %s" % cible)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sous = p.add_subparsers(dest="commande", required=True)
    c = sous.add_parser("combats")
    c.add_argument("travail")
    c.add_argument("--graine", type=int, default=424242)
    c.add_argument("--combats", type=int, default=2, help="blocs ; chacun est joue dans les deux sens")
    c.add_argument("--reference", default=CHAMPION)
    c.set_defaults(fonction=cmd_combats)
    s = sous.add_parser("situations")
    s.add_argument("travail")
    s.add_argument("--combien", type=int, default=15)
    s.add_argument("--invocations", action="store_true", help="classer aussi les tours d'invocation")
    s.set_defaults(fonction=cmd_situations)
    d = sous.add_parser("decision")
    d.add_argument("travail")
    d.add_argument("--combat", required=True)
    d.add_argument("--tour", type=int, required=True)
    # 0 est un identifiant d'entite VALIDE : la sentinelle « toutes » est -1.
    d.add_argument("--entite", type=int, required=True)
    d.add_argument("--sans-item", type=int, default=-1, dest="sans_item",
                   help="rendre aussi la meilleure suite exploree qui ne lance PAS cet item")
    d.set_defaults(fonction=cmd_decision)
    j = sous.add_parser("joue")
    j.add_argument("travail")
    j.add_argument("--combat", required=True)
    j.add_argument("--tour", type=int, required=True)
    j.add_argument("--entite", type=int, required=True)
    j.set_defaults(fonction=cmd_joue)
    args = p.parse_args()
    return args.fonction(args)


if __name__ == "__main__":
    sys.exit(main())

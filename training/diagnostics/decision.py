"""Diagnostic cible d'une decision du scoring simple.

Trois sous-commandes, un repertoire de travail, des fichiers JSON :

  combats     joue des combats eleveur aux coeurs reels contre une reference (le champion par
              defaut) et archive, pour chacun, le journal d'actions du moteur et les journaux
              des IA.
  situations  classe les tours de nos poireaux par ce qu'ils ont PAYE pour ce qu'ils ont
              obtenu : vie encaissee jusqu'a leur tour suivant, moins vie retiree pendant leur
              tour. Une defaite n'est pas une erreur ; cette liste ne fait que proposer ou
              regarder.
  decision    rejoue UN combat avec le diagnostic arme sur (tour, entite), VERIFIE que les
              actions qui precedent la decision sont celles du combat normal, et rend le
              detail : contexte, suite retenue, alternatives explorees depuis le meme etat,
              contributions par entite et par statistique, placement reellement calcule.

Le diagnostic est eteint dans le depot (DIAG_TURN = 0) : seule la copie deployee par la
sous-commande `decision` l'arme, sur une seule decision.

Usage :
  python decision.py combats   <travail> [--graine N] [--combats N] [--reference <commit>]
  python decision.py situations <travail> [--combien N]
  python decision.py decision  <travail> --combat <etiquette> --tour T --entite E
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
A_NEW_TURN, A_LEEK_TURN, A_END_TURN = 6, 7, 8
A_MOVE_TO, A_USE_CHIP, A_USE_WEAPON = 10, 12, 16
A_PLAYER_DEAD, A_KILL = 5, 11
A_LOST_LIFE, A_DAMAGE_RETURN, A_LIFE_DAMAGE, A_POISON, A_AFTEREFFECT = 101, 108, 109, 110, 111
A_NOVA_DAMAGE, A_HEAL = 107, 103
A_AI_ERROR = 1002
DEGATS = (A_LOST_LIFE, A_DAMAGE_RETURN, A_LIFE_DAMAGE, A_POISON, A_AFTEREFFECT)


# ------------------------------------------------------------------ deploiement et execution

def empreinte_travail() -> str:
    h = hashlib.sha256()
    src = DEPOT / "New_AI"
    for f in sorted(src.rglob("*.leek")):
        h.update(str(f.relative_to(src)).encode() + b"\0" + f.read_bytes())
    return h.hexdigest()[:10]


def deployer(ref: str, tour: int = 0, entite: int = 0) -> str:
    """Un commit, ou WORK pour l'arbre de travail. Un nom NEUF a chaque appel : le generateur
    recompile par date de repertoire, et resservirait sinon un binaire perime."""
    marque = "diag" if tour else "ref"
    if ref == "WORK":
        nom = "%s-work-%s-%d" % (marque, empreinte_travail(), int(time.time() * 1000) % 10 ** 9)
        dest = GEN / "test" / "ai" / "bundles" / nom
        shutil.copytree(DEPOT / "New_AI", dest)
    else:
        emp = mod_bundle.empreinte(ref)
        nom = "%s-%s-%d" % (marque, emp["sha256"][:10], int(time.time() * 1000) % 10 ** 9)
        dest = GEN / "test" / "ai" / "bundles" / nom
        mod_bundle.materialiser(ref, dest)
    if tour:
        armer(dest, tour, entite)
    maintenant = time.time()
    for f in dest.rglob("*"):
        if f.is_file():
            os.utime(f, (maintenant, maintenant))
    return "test/ai/bundles/%s/Main.leek" % nom


def armer(dest: Path, tour: int, entite: int) -> None:
    """Arme le diagnostic dans la COPIE deployee : deux constantes, rien d'autre."""
    fichier = dest / "Scoring" / "Scoring.leek"
    texte = fichier.read_text(encoding="utf-8")
    for ancre, remplacement in (("global DIAG_TURN = 0", "global DIAG_TURN = %d" % tour),
                                ("global DIAG_ENTITY = 0", "global DIAG_ENTITY = %d" % entite)):
        assert texte.count(ancre) == 1, "ancre absente : %s" % ancre
        texte = texte.replace(ancre, remplacement)
    fichier.write_text(texte, encoding="utf-8")


def jouer(chemins: list[Path], timeout: int = 7200) -> list[dict]:
    """Joue les scenarios et rend la sortie COMPLETE du moteur : actions, journaux, vainqueur."""
    cp = os.pathsep.join([str(TRAINING / "runs" / "v3" / ".build-fid"), str(GEN / "generator.jar"),
                          str(GEN / "leekscript" / "leekscript.jar")])
    # Sans forcer l'encodage, la JVM ecrit les journaux dans la page de code Windows et
    # tous les accents des lignes de diagnostic reviennent casses.
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

def journaux(brut: dict) -> list[tuple[int, str]]:
    """Les lignes de debug des IA, dans l'ordre du moteur : [(entite, texte)]."""
    lignes = []
    for _f, par_entite in ((brut.get("outcome") or {}).get("logs") or {}).items():
        for _a, journal in par_entite.items():
            for l in journal:
                if len(l) >= 3 and isinstance(l[2], str):
                    lignes.append((l[0], l[2]))
    return lignes


def erreurs_systeme(brut: dict) -> list[list]:
    sorties = []
    for _f, par_entite in ((brut.get("outcome") or {}).get("logs") or {}).items():
        for _a, journal in par_entite.items():
            for l in journal:
                if len(l) >= 4 and l[1] in (7, 8):
                    sorties.append(l)
    return sorties


def actions_par_tour(brut: dict) -> list[dict]:
    """Decoupe le journal d'actions du moteur en tours d'entite.

    Rend, pour chaque tour joue : le numero de tour, l'entite, ses actions brutes, la vie
    retiree a chaque entite pendant ce tour. Le moteur emet NEW_TURN puis un LEEK_TURN par
    entite qui joue ; tout ce qui suit appartient a cette entite jusqu'au LEEK_TURN suivant.
    """
    fight = (brut.get("outcome") or {}).get("fight") or {}
    # Le moteur n'emet pas de NEW_TURN pour le PREMIER tour : il commence a 1 d'office.
    tours, tour, courant = [], 1, None
    for act in fight.get("actions") or []:
        if not act:
            continue
        type_ = act[0]
        if type_ == A_NEW_TURN:
            tour = act[1] if len(act) > 1 else tour + 1
            continue
        if type_ == A_LEEK_TURN:
            courant = {"tour": tour, "entite": act[1], "actions": [], "degats": collections.Counter(),
                       "soins": collections.Counter(), "morts": []}
            tours.append(courant)
            continue
        if courant is None:
            continue
        courant["actions"].append(act)
        if type_ in DEGATS and len(act) >= 3:
            courant["degats"][act[1]] += act[2]
        elif type_ == A_NOVA_DAMAGE and len(act) >= 3:
            courant["degats"][act[1]] += 0
        elif type_ == A_HEAL and len(act) >= 3:
            courant["soins"][act[1]] += act[2]
        elif type_ == A_PLAYER_DEAD and len(act) >= 2:
            courant["morts"].append(act[1])
    return tours


def entites(brut: dict) -> dict[int, dict]:
    fight = (brut.get("outcome") or {}).get("fight") or {}
    return {e["id"]: e for e in fight.get("leeks") or []}


def resume_action(act: list, noms: dict[int, str]) -> str:
    type_ = act[0]
    if type_ == A_MOVE_TO:
        return "deplacement -> %s" % (act[2] if len(act) > 2 else "?")
    if type_ == A_USE_CHIP:
        return "puce %s sur %s" % (act[2] if len(act) > 2 else "?", act[3] if len(act) > 3 else "?")
    if type_ == A_USE_WEAPON:
        return "arme sur %s" % (act[2] if len(act) > 2 else "?")
    if type_ == A_PLAYER_DEAD:
        return "mort de %s" % noms.get(act[1], act[1])
    return "action %s" % type_


# ------------------------------------------------------------------ sous-commande : combats

def cmd_combats(args) -> int:
    travail = Path(args.travail).resolve()
    travail.mkdir(parents=True, exist_ok=True)
    builds = mod_sc.charger_builds(TRAINING / "data" / "builds_v3.jsonl")
    ia_nous = deployer("WORK")
    ia_eux = deployer(args.reference)
    chemins, etiquettes = [], []
    blocs = list(mod_sc.plan_de_blocs("farmer", ["x"], args.combats, args.graine, builds,
                                      vague="diagnostic"))
    for b in blocs:
        for cote, (g, d) in (("gauche", (ia_nous, ia_eux)), ("droite", (ia_eux, ia_nous))):
            chemin = travail / ("scenario-%d-%s.json" % (b.indice, cote))
            mod_sc.ecrire(chemin, mod_sc.scenario(b, builds, g, d))
            chemins.append(chemin)
            etiquettes.append("%d-%s" % (b.indice, cote))
    t0 = time.time()
    sorties = jouer(chemins)
    index = {"graine": args.graine, "reference": args.reference, "nous": ia_nous, "eux": ia_eux,
             "empreinte_travail": empreinte_travail(), "combats": []}
    for etiquette, chemin, brut in zip(etiquettes, chemins, sorties):
        cible = travail / ("combat-%s.json" % etiquette)
        cible.write_text(json.dumps(brut), encoding="utf-8")
        ents = entites(brut)
        # Notre camp : celui dont les entites portent NOTRE chemin d'IA dans le scenario.
        source = json.loads(chemin.read_text(encoding="utf-8"))
        notre_camp = 1 if source["entities"][0][0]["ai"] == ia_nous else 2
        erreurs = erreurs_systeme(brut)
        index["combats"].append({
            "etiquette": etiquette, "scenario": chemin.name, "fichier": cible.name,
            "notre_camp": notre_camp, "vainqueur": brut.get("winner"),
            "tours": (brut.get("outcome") or {}).get("duration"),
            "erreurs_systeme": len(erreurs),
            "entites": {str(i): {"nom": e.get("name"), "camp": e.get("team")} for i, e in ents.items()},
        })
        print("combat %-10s | camp %d | vainqueur %s | tours %s | erreurs systeme %d"
              % (etiquette, notre_camp, brut.get("winner"),
                 (brut.get("outcome") or {}).get("duration"), len(erreurs)))
    (travail / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print("%d combats en %.0f s -> %s" % (len(sorties), time.time() - t0, travail))
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
        # Les invocations meurent par dizaines sans que ce soit une erreur : seuls les
        # poireaux sont classes, sauf demande contraire.
        vises = notre if args.invocations else {i for i in notre if not ents[i].get("summon")}
        tours = actions_par_tour(brut)
        # Vie encaissee par une entite ENTRE la fin de son tour et son tour suivant.
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

def signature_actions(brut: dict, tour: int, entite: int) -> list:
    """Les actions du moteur JUSQU'AU debut du tour vise, sous une forme comparable."""
    fight = (brut.get("outcome") or {}).get("fight") or {}
    prefixe, courant_tour = [], 1
    for act in fight.get("actions") or []:
        if not act:
            continue
        if act[0] == A_NEW_TURN:
            courant_tour = act[1] if len(act) > 1 else courant_tour + 1
        if act[0] == A_LEEK_TURN and courant_tour == tour and len(act) > 1 and act[1] == entite:
            return prefixe
        if act[0] == A_AI_ERROR:
            continue
        prefixe.append(act)
    return prefixe


def cmd_decision(args) -> int:
    travail = Path(args.travail).resolve()
    index = json.loads((travail / "index.json").read_text(encoding="utf-8"))
    combat = next(c for c in index["combats"] if c["etiquette"] == args.combat)
    if index["empreinte_travail"] != empreinte_travail():
        print("ATTENTION : l'arbre de travail a change depuis les combats de reference")
    reference = json.loads((travail / combat["fichier"]).read_text(encoding="utf-8"))

    # Le MEME scenario, seuls les chemins d'IA de notre camp pointent la copie armee.
    ia_diag = deployer("WORK", args.tour, args.entite)
    source = json.loads((travail / combat["scenario"]).read_text(encoding="utf-8"))
    nous = index["nous"]
    remplaces = 0
    for groupe in source["entities"]:
        for e in groupe:
            if e["ai"] == nous:
                e["ai"] = ia_diag
                remplaces += 1
    assert remplaces > 0, "aucune entite ne portait notre IA"
    chemin = travail / ("rejeu-%s-t%d-e%d.json" % (args.combat, args.tour, args.entite))
    chemin.write_text(json.dumps(source), encoding="utf-8")
    brut = jouer([chemin])[0]

    # L'instrumentation coute des operations : la trajectoire peut diverger. On ne garde la
    # decision que si tout ce qui la precede est identique au combat normal.
    avant_ref = signature_actions(reference, args.tour, args.entite)
    avant_diag = signature_actions(brut, args.tour, args.entite)
    divergence = None
    if len(avant_ref) != len(avant_diag):
        divergence = "longueurs differentes : %d contre %d" % (len(avant_ref), len(avant_diag))
    else:
        for i, (a, b) in enumerate(zip(avant_ref, avant_diag)):
            if a != b:
                divergence = "action %d : %s contre %s" % (i, a, b)
                break
    if divergence is None:
        print("trajectoire identique au combat normal jusqu'a la decision (%d actions)" % len(avant_ref))
    else:
        print("DIVERGENCE due a l'instrumentation avant la decision : %s" % divergence)

    lignes = [t for e, t in journaux(brut) if e == args.entite and t.startswith(("[diag]", "[score]"))]
    if not lignes:
        print("aucune ligne de diagnostic : le tour %d de l'entite %d n'a pas ete joue"
              % (args.tour, args.entite))
    sortie = {"combat": args.combat, "tour": args.tour, "entite": args.entite,
              "divergence": divergence, "actions_avant": len(avant_ref),
              "erreurs_systeme": len(erreurs_systeme(brut)), "lignes": lignes}
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
    d.add_argument("--entite", type=int, required=True)
    d.set_defaults(fonction=cmd_decision)
    args = p.parse_args()
    return args.fonction(args)


if __name__ == "__main__":
    sys.exit(main())

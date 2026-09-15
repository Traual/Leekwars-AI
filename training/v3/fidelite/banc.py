"""Banc de fidelite des consequences sur le generateur 3.00.

Chaque scenario pose des entites sur des cellules choisies d'une carte sans obstacle, impose a
certaines un script d'actions, et la sonde compare apres chaque action l'etat predit par l'IA
(jet minimal et jet maximal) a l'etat relu dans le moteur.

Usage : python banc.py [WORK|<commit>] [filtre de nom de scenario]
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ICI = Path(__file__).resolve().parent
TRAINING = ICI.parents[1]
DEPOT = TRAINING.parent
sys.path.insert(0, str(TRAINING))

import bundle as mod_bundle      # noqa: E402

GEN = Path("C:/Users/aurel/Desktop/lw-gen-300")
BUILD = TRAINING / "runs" / "v3" / ".build-fid"
SORTIES = TRAINING / "runs" / "v3" / "fidelite"
PREFIXE = "__FIDELITE__\t"

sys.path.insert(0, str(ICI))
from scenarios_fidelite import SCENARIOS as _BASE  # noqa: E402
from scenarios_300 import SCENARIOS_300  # noqa: E402
from scenarios_complets import COMPLETS  # noqa: E402
SCENARIOS = _BASE + SCENARIOS_300 + COMPLETS

CHIPS = json.load(open(GEN / "data" / "chips.json", encoding="utf-8"))
WEAPONS = json.load(open(GEN / "data" / "weapons.json", encoding="utf-8"))
PUCE = {v["name"]: int(k) for k, v in CHIPS.items()}
ARME = {v["name"]: v["item"] for v in WEAPONS.values()}


def cellule(x: int, y: int) -> int:
    for cid in range(613):
        xw, yw = cid % 35, cid // 35
        cy = yw - xw % 18
        cx = (cid - 17 * cy) // 18
        if cx == x and cy == y:
            return cid
    raise ValueError("cellule hors carte (%d, %d)" % (x, y))


def item(nom: str) -> int:
    if nom in PUCE:
        return PUCE[nom]
    if nom in ARME:
        return ARME[nom]
    raise KeyError(nom)


def litteral(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(litteral(x) for x in v) + "]"
    if isinstance(v, dict):
        if not v:
            return "[:]"
        return "[" + ", ".join("%s: %s" % (litteral(k), litteral(x)) for k, x in v.items()) + "]"
    raise TypeError(type(v))


def action_ls(a):
    kind = a[0]
    if kind in ("chip", "weapon", "summon"):
        return [kind, item(a[1]), cellule(*a[2])]
    if kind == "move":
        return [kind, cellule(*a[1])]
    if kind == "gen":
        return [kind]
    raise ValueError(a)


def deployer(ref: str) -> Path:
    if ref == "WORK":
        src = DEPOT / "New_AI"
        h = hashlib.sha256()
        for f in sorted(src.rglob("*.leek")):
            h.update(str(f.relative_to(src)).encode() + b"\0" + f.read_bytes())
        nom = "fid-%s-%d" % (h.hexdigest()[:10], int(time.time() * 1000))
        dest = GEN / "test" / "ai" / "bundles" / nom
        shutil.copytree(src, dest)
    else:
        emp = mod_bundle.empreinte(ref)
        nom = "fid-%s-%d" % (emp["sha256"][:10], int(time.time() * 1000))
        dest = GEN / "test" / "ai" / "bundles" / nom
        mod_bundle.materialiser(ref, dest)
    shutil.copy(ICI / "FideliteProbe.leek", dest / "FideliteProbe.leek")
    now = time.time()
    for f in dest.rglob("*"):
        if f.is_file():
            os.utime(f, (now, now))
    return dest


def entite(e: dict, eid: int, camp: int, ia: str) -> dict:
    s = {"life": 3000, "strength": 0, "wisdom": 0, "agility": 0, "resistance": 0,
         "science": 0, "magic": 0, "frequency": 100, "cores": 100, "ram": 50, "tp": 30, "mp": 8}
    s.update(e.get("stats", {}))
    return {
        "id": eid, "ai": ia, "ai_owner": camp, "name": e["nom"], "type": 0,
        "farmer": camp, "team": camp, "level": 301,
        "life": s["life"], "strength": s["strength"], "wisdom": s["wisdom"],
        "agility": s["agility"], "resistance": s["resistance"], "science": s["science"],
        "magic": s["magic"], "frequency": s["frequency"], "cores": s["cores"], "ram": s["ram"],
        "tp": s["tp"], "mp": s["mp"],
        "weapons": [ARME[w] for w in e.get("armes", [])],
        "chips": [PUCE[c] for c in e.get("puces", [])],
    }


def construire(sc: dict, dest: Path) -> dict:
    script = {}
    for e in sc["entites"]:
        if "script" in e:
            script[e["nom"]] = {int(t): [action_ls(a) for a in acts] for t, acts in e["script"].items()}
    main = dest / ("Fid_%s.leek" % sc["nom"])
    main.write_text(
        'include("includes.leek")\ninclude("FideliteProbe.leek")\n'
        "global FID_SCRIPT = %s\n"
        "if (getTurn() == 1) Preload.InitEverything()\n"
        "FideliteProbe.play(FID_SCRIPT)\n" % litteral(script), encoding="utf-8")
    rel = "test/ai/bundles/%s/%s" % (dest.name, main.name)
    rel_main = "test/ai/bundles/%s/Main.leek" % dest.name
    groupes = [[], []]
    cellules = [[], []]
    for i, e in enumerate(sc["entites"]):
        camp = e["camp"]
        ia = rel_main if e.get("ia") == "main" else rel
        groupes[camp - 1].append(entite(e, camp * 10000 + i + 1, camp, ia))
        cellules[camp - 1].append(cellule(*e["cellule"]))
    return {
        "farmers": [{"id": 1, "name": "gauche", "country": "fr"},
                    {"id": 2, "name": "droite", "country": "fr"}],
        "teams": [{"id": 1, "name": "gauche"}, {"id": 2, "name": "droite"}],
        "entities": groupes,
        "fight_type": 1, "fight_context": 2,
        "random_seed": sc.get("graine", 12345),
        "max_turns": sc.get("tours", 3),
        "map": {"obstacles": {}, "team1": cellules[0], "team2": cellules[1]},
    }


def compiler() -> str:
    BUILD.mkdir(parents=True, exist_ok=True)
    cp = os.pathsep.join([str(GEN / "generator.jar"), str(GEN / "leekscript" / "leekscript.jar")])
    classe = BUILD / "training" / "fidelite" / "FideliteRunner.class"
    src = ICI / "FideliteRunner.java"
    if not classe.exists() or classe.stat().st_mtime < max(src.stat().st_mtime, (GEN / "generator.jar").stat().st_mtime):
        subprocess.run(["javac", "-cp", cp, "-d", str(BUILD), str(src)], check=True)
    return os.pathsep.join([str(BUILD), cp])


def comparer(ligne: dict) -> list[str]:
    ecarts = []
    avant = set(ligne["avant"].keys())
    pmin, pmax, reel = ligne["min"], ligne["max"], ligne["reel"]
    nouveaux_p = sorted((k for k in pmin if k not in avant), key=int)
    nouveaux_r = sorted((k for k in reel if k not in avant), key=int)
    paires = [(k, k, k) for k in sorted(avant, key=int)]
    renomme = {}
    for a, b in zip(nouveaux_p, nouveaux_r):
        paires.append((a, a, b))
        renomme[a] = b
    if len(nouveaux_p) != len(nouveaux_r):
        ecarts.append("entites nees : predit %s, moteur %s" % (nouveaux_p, nouveaux_r))

    def cles(lignes):
        # Le lanceur d'une ligne posee par une entite nee porte son id virtuel cote prediction.
        out = {}
        for k, v in (lignes if isinstance(lignes, dict) else {}).items():
            morceaux = k.split("|")
            if morceaux[1] in renomme:
                morceaux[1] = renomme[morceaux[1]]
            out["|".join(morceaux)] = v
        return out
    for kp, kx, kr in paires:
        a, b, r = pmin.get(kp), pmax.get(kx), reel.get(kr)
        if a is None or r is None:
            ecarts.append("entite %s absente (predit %s, moteur %s)" % (kr, a is not None, r is not None))
            continue
        for champ in a:
            if champ == "EFFECTS":
                la, lb, lr = cles(a[champ]), cles(b[champ]), cles(r[champ])
                for k in sorted(set(la) | set(lr)):
                    va, vb, vr = la.get(k, 0), lb.get(k, 0), lr.get(k, 0)
                    # le lanceur d'une ligne posee par une entite nee est son id REEL
                    if not (min(va, vb) <= vr <= max(va, vb)):
                        ecarts.append("%s EFFECTS[%s] predit %s..%s moteur %s" % (kr, k, va, vb, vr))
                continue
            va, vb, vr = a[champ], b[champ], r[champ]
            if champ == "CELL" or champ == "STATES":
                if va != vr:
                    ecarts.append("%s %s predit %s moteur %s" % (kr, champ, va, vr))
                continue
            if not (min(va, vb) <= vr <= max(va, vb)):
                ecarts.append("%s %s predit %s..%s moteur %s" % (kr, champ, va, vb, vr))
    return ecarts


def main():
    ref = sys.argv[1] if len(sys.argv) > 1 else "WORK"
    filtre = sys.argv[2] if len(sys.argv) > 2 else ""
    dest = deployer(ref)
    cp = compiler()
    travail = Path(tempfile.mkdtemp(prefix="fid-"))
    choisis = [sc for sc in SCENARIOS if filtre in sc["nom"]]
    fichiers = []
    for sc in choisis:
        p = travail / ("%s.json" % sc["nom"])
        p.write_text(json.dumps(construire(sc, dest)), encoding="utf-8")
        fichiers.append(p)
    proc = subprocess.run(["java", "-cp", cp, "training.fidelite.FideliteRunner", *map(str, fichiers)],
                          cwd=str(GEN), capture_output=True, text=True, encoding="utf-8", errors="replace")
    SORTIES.mkdir(parents=True, exist_ok=True)
    total_ecarts = 0
    for ligne in proc.stdout.splitlines():
        if not ligne.startswith(PREFIXE):
            continue
        idx, charge = ligne[len(PREFIXE):].split("\t", 1)
        sc = choisis[int(idx)]
        brut = json.loads(charge)
        (SORTIES / ("%s.json" % sc["nom"])).write_text(charge, encoding="utf-8")
        if "runner_error" in brut or "exception" in brut:
            print("[%s] ERREUR %s" % (sc["nom"], brut.get("runner_error") or brut.get("exception")))
            total_ecarts += 1
            continue
        out = brut["outcome"]
        fid, erreurs = [], []
        for _f, per in out["logs"].items():
            for _a, lignes in per.items():
                for l in lignes:
                    if len(l) >= 3 and isinstance(l[2], str) and l[2].startswith("FID "):
                        fid.append(json.loads(l[2][4:]))
                    elif len(l) >= 4 and l[1] in (7, 8):
                        erreurs.append(l)
        gen = []
        for _f, per in out["logs"].items():
            for _a, lignes in per.items():
                for l in lignes:
                    if len(l) >= 3 and isinstance(l[2], str) and l[2].startswith("GEN "):
                        gen.append(json.loads(l[2][4:]))
        for g in gen:
            noms = {str(PUCE.get(n, ARME.get(n))): n for n in list(PUCE) + list(ARME)}
            print("   generation %s tour %s : %s" % (g["nom"], g["tour"],
                  {noms.get(str(k), k): v for k, v in (g["items"] or {}).items()}))
        attendues = sum(1 for e in sc["entites"] for acts in e.get("script", {}).values()
                        for a in acts if a[0] != "gen")
        print("[%s] %d/%d etapes sondees, %d erreur(s) systeme, vainqueur %s"
              % (sc["nom"], len(fid), attendues, len(erreurs), brut.get("winner")))
        for e in erreurs[:5]:
            print("   systeme", e)
        for f in fid:
            if f["code"] is not None and f["code"] <= 0 and f["action"][0] != "move":
                # Action refusee par le moteur : la prediction d'un lancer illegal ne se compare pas.
                print("   tour %s etape %s %s : REFUSEE PAR LE MOTEUR (code %s)" % (f["tour"], f["etape"], f["action"], f["code"]))
                continue
            ecarts = comparer(f)
            total_ecarts += len(ecarts)
            etat = "OK" if not ecarts else "%d ECART(S)" % len(ecarts)
            print("   tour %s etape %s %s code %s : %s" % (f["tour"], f["etape"], f["action"], f["code"], etat))
            for e in ecarts:
                print("      " + e)
        if len(fid) != attendues:
            total_ecarts += 1
            print("   ETAPES MANQUANTES")
    if proc.returncode != 0 and not proc.stdout:
        print(proc.stderr[-3000:])
    print("TOTAL ECARTS %d" % total_ecarts)


if __name__ == "__main__":
    main()

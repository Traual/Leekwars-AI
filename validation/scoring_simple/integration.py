"""Real consequences and chains, incremental score, resurrection and placement."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import runtime


def cell(x, y):
    for cid in range(613):
        cy = cid // 35 - (cid % 35) % 18
        cx = (cid - 17 * cy) // 18
        if (cx, cy) == (x, y):
            return cid
    raise ValueError((x, y))


def scenario(dest, runtime_dir, cores=100):
    chips = {v['name']: int(k) for k, v in json.loads((runtime_dir/'data/chips.json').read_text()).items()}
    weapons = {v['name']: v['item'] for v in json.loads((runtime_dir/'data/weapons.json').read_text()).values()}
    groups = [[], []]
    for side in range(2):
        for i in range(2):
            groups[side].append(dict(id=side*10000+i+1, farmer=side+1, team=side+1, ai_owner=side+1,
                name='probe' if side == i == 0 else f'p{side}-{i}', type=0, level=301,
                ai=f'test/ai/bundles/{dest.name}/' + ('Sonde.leek' if side == i == 0 else 'Main.leek'),
                life=3000, strength=400, wisdom=300, agility=200, resistance=150, science=200, magic=250,
                frequency=100, cores=cores, ram=50, tp=28, mp=6,
                weapons=[weapons[x] for x in ['machine_gun','b_laser','pistol','grenade_launcher','katana']],
                chips=[chips[x] for x in ['toxin','armor','protein','vaccine','bandage','shield','liberation',
                       'antidote','jump','thorn','motivation','inversion','lightning','rockfall','spark',
                       'stalactite','meteorite','flame','resurrection','puny_bulb','corn']]))
    return dict(farmers=[dict(id=i,name=str(i),country='fr') for i in (1,2)],
                teams=[dict(id=i,name=str(i)) for i in (1,2)], entities=groups,
                fight_type=1, fight_context=2, random_seed=424242, max_turns=2,
                map=dict(obstacles={},team1=[cell(14,0),cell(13,2)],team2=[cell(19,0),cell(20,2)]))


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--runtime',required=True,type=Path)
    ap.add_argument('--output',type=Path,default=Path('validation/scoring_simple/results/integration.json'))
    args=ap.parse_args(); rt=args.runtime.resolve()
    dest=runtime.bundle(rt)
    shutil.copyfile(Path(__file__).with_name('IntegrationProbe.leek'),dest/'ScoringProbe.leek')
    (dest/'Sonde.leek').write_text('include("includes.leek")\ninclude("ScoringProbe.leek")\n'
        'if (getTurn() == 1) Preload.InitEverything()\n'
        'PROBE_CASTS = [[CHIP_TOXIN,"ennemi"],[CHIP_ARMOR,"moi"],[CHIP_PROTEIN,"moi"],[CHIP_VACCINE,"allie"]]\n'
        'ScoringProbe.play()\n', encoding='utf-8')
    path=rt/'integration.json'; path.write_text(json.dumps(scenario(dest,rt)))
    result=runtime.run(rt,[path],rt/'integration-result.json')[0]
    checks=[json.loads(s[len('RECEPTION '):]) for s in runtime.messages(result,'RECEPTION ')]
    errors=[e for e in runtime.logs(result) if len(e)>3 and e[1] in (7,8)]
    for c in checks: print(c['test'],c['ok'],c['detail'],flush=True)
    print('errors',errors[:10])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(dict(bundle=dest.name,checks=checks,errors=errors,diagnostic_cores=100),ensure_ascii=False,indent=2),encoding='utf-8')
    if len(checks)<8 or errors or any(not c['ok'] for c in checks): raise SystemExit(1)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()

"""120 native microgames. The oracle never reads a scoring coefficient.

Each root has two legal scripted turns, continued against a fixed response for 4-6
turns. We compare the shipped LeekScript ranking with final HP + a decisive win
bonus, averaged on paired seeds. This is a conditional rollout oracle, NOT a proof
of optimal play against an adapting opponent. Cases and disagreements are retained.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import shutil
import statistics
import sys
import time
import runtime
from integration import cell


def catalogue(rt):
    chips = {v['name']: int(k) for k,v in json.loads((rt/'data/chips.json').read_text()).items()}
    weapons = {v['name']: v['item'] for v in json.loads((rt/'data/weapons.json').read_text()).values()}
    return chips, weapons


def cases(rt):
    c,w = catalogue(rt)
    # No optimization of this grid against the score: all variants are retained.
    families = [
        ('force', 'protein', None, [], None),
        ('sagesse', 'knowledge', 'bandage', ['bandage'], None),
        ('agilite', 'stretching', None, [], None),
        ('resistance', 'solidification', 'armor', ['armor','wall'], None),
        ('science', 'prism', 'reflexes', ['reflexes','rage'], None),
        ('magie', 'wizardry', 'toxin', ['toxin'], None),
        ('ressources', 'motivation', 'adrenaline', [], None),
        ('vie_max', 'elevation', 'bandage', ['bandage'], None),
        ('boucliers', 'armor', 'fortress', [], None),
        ('renvoi', 'thorn', 'shield', [], None),
        ('periodique', 'vaccine', 'bandage', ['bandage'], None),
        ('placement', None, None, [], None),
    ]
    result=[]
    for family,a,b,follow,setup in families:
        for i in range(10):
            strength=[100,250,450,700,1000][i%5]
            stats=dict(life=2300+150*(i%4), strength=strength, wisdom=[0,200,500,800][i%4],
                       agility=[0,300,700,900,1000][i%5], resistance=[0,100,300,700][i%4],
                       science=[0,200,500][i%3], magic=[0,300,700][i%3], tp=12+(i%3)*2,mp=4)
            if family=='agilite': stats['agility']=[0,300,650,800,900,950,999,1000,1100,1500][i]
            options=[]
            for chip in (a,b):
                options.append([] if chip is None else [['chip',c[chip],chip=='toxin']])
            if family=='placement':
                options=[[['move',cell(10,0)]],[['move',cell(16,2)]]]
            kit=set([x for x in (a,b,setup) if x]+follow)
            # Keep the offensive kit identical between the two alternatives.
            weapon='pistol' if i%2==0 else 'machine_gun'
            if family in ('sagesse','vie_max','periodique'): stats['wisdom']=150+i*100
            result.append(dict(id=f'{family}-{i:02}',family=family,index=i, stats=stats,
                foe=dict(life=2700, strength=350+80*i, wisdom=0, agility=150, resistance=0,
                         science=0,magic=0,tp=12,mp=0),
                chips=[c[x] for x in sorted(kit)],weapon=w[weapon],foe_weapon=w['pistol'],
                config=dict(options=options,continuation=[[c[x], x=='toxin'] for x in follow],
                            setup=c[setup] if setup else 0,placement=True),
                horizon=4+i%3))
    return result


def scenario(case, option, seed, dest):
    entities=[]
    for side in (1,2):
        e=dict(case['stats'] if side==1 else case['foe'])
        e.update(id=side,farmer=side,team=side,ai_owner=side,name=f"{case['id']}|{option if side==1 else 'foe'}",
                 type=0,level=301,cores=18,ram=50,frequency=200 if side==1 else 100,
                 chips=case['chips'] if side==1 else [], weapons=[case['weapon'] if side==1 else case['foe_weapon']],
                 ai=f'test/ai/bundles/{dest.name}/Rollout.leek')
        entities.append([e])
    return dict(farmers=[dict(id=i,name=str(i),country='fr') for i in (1,2)],
                teams=[dict(id=i,name=str(i)) for i in (1,2)],entities=entities,
                fight_type=0,fight_context=2,random_seed=seed,max_turns=case['horizon'],
                map=dict(obstacles={},team1=[cell(14,0)],team2=[cell(18,0)]))


def terminal_value(result):
    # Actual terminal engine state, including a lethal tick before an AI can run.
    # Entity identity, never array order or debug chronology, determines the side.
    life={row[0]:row[1] for row in result['terminal']}
    value=life[0]-life[1]
    if life[0]>0 and life[1]<=0: value+=10000
    if life[0]<=0 and life[1]>0: value-=10000
    return value


def analyse(cases_, records):
    rows=[]
    for case in cases_:
        rs=[r for r in records if r['case']==case['id']]
        by_seed={}
        for r in rs: by_seed.setdefault(r['seed'],{})[r['option']]=r
        valid=[x for x in by_seed.values() if len(x)==2 and all(v['valid'] for v in x.values())]
        for pair in valid:
            if pair[0]['root'] != pair[1]['root']: raise AssertionError(f"different roots {case['id']}")
        score_diff=[p[0]['score'][0]-p[1]['score'][0] for p in valid]
        truth_diff=[p[0]['terminal']-p[1]['terminal'] for p in valid]
        sd=statistics.mean(score_diff) if valid else None
        td=statistics.mean(truth_diff) if valid else None
        se=statistics.stdev(truth_diff)/math.sqrt(len(valid)) if len(valid)>1 else None
        critical=math.inf
        # Conservative t(.975) table by sample count, for descriptive diagnostics.
        # These comparisons are not independent promotion tests.
        for n,t in [(2,12.707),(3,4.303),(4,3.183),(5,2.777),(6,2.571),(7,2.447),
                    (8,2.365),(10,2.263),(12,2.202),(16,2.132),(24,2.069),(32,2.040),(64,2.000)]:
            if len(valid)>=n:critical=t
        robust=td is not None and se is not None and abs(td)>critical*se+1
        rows.append(dict(id=case['id'],family=case['family'],pairs=len(valid),
            score_difference=sd,oracle_difference=td,oracle_se=se,robust=robust,
            agrees=None if not robust else sd*td>0, invalid_pairs=len(by_seed)-len(valid)))
    return rows


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--runtime',required=True,type=Path)
    ap.add_argument('--seeds',default=8,type=int);ap.add_argument('--limit',type=int)
    ap.add_argument('--output',type=Path,default=Path('validation/scoring_astra/results'))
    args=ap.parse_args();rt=args.runtime.resolve();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    grid=cases(rt)
    if args.limit: grid=grid[:args.limit]
    dest=runtime.bundle(rt,'oracle')
    shutil.copyfile(Path(__file__).with_name('RolloutProbe.leek'),dest/'RolloutProbe.leek')
    (dest/'Cases.leek').write_text('global ROLLOUT_CASES = '+runtime.literal({x['id']:x['config'] for x in grid})+'\n',encoding='utf-8')
    (dest/'Rollout.leek').write_text('include("RolloutProbe.leek")\nRolloutProbe.play()\n'
        'var finalLife=[]\nfor(var id in getAllies()+getEnemies()) push(finalLife,[id,getLife(id)])\n'
        'finalLife=arraySort(finalLife,(a,b)->a[0]-b[0])\n'
        'debug("ORACLE_FINAL " + jsonEncode(["action":getTurn()*1000+getEntity(),"life":finalLife]))\n',encoding='utf-8')
    (out/'cases.json').write_text(json.dumps(grid,ensure_ascii=False,indent=2),encoding='utf-8')
    jobs=[]
    inputs=rt/'oracle-inputs';inputs.mkdir(exist_ok=True)
    for case in grid:
        for seed in range(args.seeds):
            for option in (0,1):
                value=202609210+seed*10007+case['index']*37
                path=inputs/f"{case['id']}-{seed}-{option}.json"
                path.write_text(json.dumps(scenario(case,option,value,dest)))
                jobs.append((case,seed,option,path))
    records=[]; started=time.time()
    for start in range(0,len(jobs),128):
        batch=jobs[start:start+128]
        results=runtime.run(rt,[j[3] for j in batch],rt/f'oracle-batch-{start}.json',timeout=240,
                            runner='validation.astra.OracleRunner')
        for (case,seed,option,path),result in zip(batch,results):
            scores=runtime.messages(result,'ORACLE_SCORE ')
            roots=runtime.messages(result,'ORACLE_ROOT ')
            codes=runtime.messages(result,'ORACLE_PREFIX ')
            errors=[x for x in runtime.logs(result) if len(x)>3 and x[1] in (7,8)]
            prefix=json.loads(codes[0].split(' ',1)[1]) if codes else []
            valid=len(scores)==1 and len(roots)==1 and not errors and all(x>0 for x in prefix)
            records.append(dict(case=case['id'],seed=seed,option=option,valid=valid,
                root=json.loads(roots[0].split(' ',1)[1]) if roots else None,
                score=json.loads(scores[0].split(' ',1)[1]) if scores else None,
                terminal=terminal_value(result),prefix=prefix,errors=errors[:4]))
        (out/'observations.json').write_text(json.dumps(records,ensure_ascii=False),encoding='utf-8')
        print(f'{min(start+128,len(jobs))}/{len(jobs)} fights; {time.time()-started:.1f}s',flush=True)
    rows=analyse(grid,records)
    report=dict(engine_sha256=runtime.digest(rt/'generator.jar'),leekscript_sha256=runtime.digest(rt/'leekscript/leekscript.jar'),
        bundle=dest.name,seeds=args.seeds,cases=len(grid),fights=len(records),seconds=time.time()-started,
        robust=sum(r['robust'] for r in rows),agree=sum(r['agrees'] is True for r in rows),
        disagree=sum(r['agrees'] is False for r in rows),invalid=sum(not r['valid'] for r in records),rows=rows)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print({k:v for k,v in report.items() if k!='rows'})
    for r in rows:
        if r['agrees'] is False or r['invalid_pairs']: print(r)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()

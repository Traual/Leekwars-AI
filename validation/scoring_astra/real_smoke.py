"""Complete fights on saved builds, unchanged cores/stats; no promotion or registry."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import sys
import time
import runtime

sys.path.insert(0,str(runtime.ROOT/'training'))
import scenarios


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--runtime',required=True,type=Path)
    ap.add_argument('--builds',required=True,type=Path);ap.add_argument('--blocks',default=2,type=int)
    ap.add_argument('--output',type=Path,default=Path('validation/scoring_astra/results/smoke.json'))
    args=ap.parse_args();rt=args.runtime.resolve()
    current=runtime.bundle(rt,'full')
    baseline=runtime.bundle(rt,'baseline',ref='3024424')
    builds=scenarios.charger_builds(args.builds.resolve())
    paths=[];labels=[]
    for fmt in ('solo','farmer','team'):
        for block in scenarios.plan_de_blocs(fmt,['baseline'],args.blocks,20260921,builds,vague='astra-smoke'):
            for mirror in (False,True):
                c=f'test/ai/bundles/{current.name}/Main.leek'
                b=f'test/ai/bundles/{baseline.name}/Main.leek'
                scenario=scenarios.scenario(block,builds,b if mirror else c,c if mirror else b)
                path=rt/f'full-{fmt}-{block.indice}-{int(mirror)}.json'
                path.write_text(json.dumps(scenario),encoding='utf-8')
                paths.append(path);labels.append((fmt,block.indice,mirror,scenario))
    rows=[];start=time.time()
    for path,(fmt,index,mirror,scenario) in zip(paths,labels):
        result=runtime.run(rt,[path],path.with_name(path.stem+'-result.json'),timeout=180)[0]
        fight=result['outcome']['fight'];leeks={x['id']:x for x in fight['leeks']}
        counts=Counter(a[1] for a in fight['actions'] if a[0]==7)
        operations={int(k):v for k,v in fight.get('ops',{}).items()}
        errors=[x for x in runtime.logs(result) if len(x)>3 and x[1] in (7,8)]
        aborts=Counter(a[1] for a in fight['actions'] if a[0]==1002)
        candidate_team=2 if mirror else 1
        entities=[]
        for eid,e in leeks.items():
            entities.append(dict(id=eid,candidate=e['team']==candidate_team,summon=e.get('summon',False),
                turns=counts[eid],operations=operations.get(eid,0),aborts=aborts[eid]))
        rows.append(dict(format=fmt,block=index,mirror=mirror,winner=result['winner'],candidate_team=candidate_team,
            duration=result['outcome'].get('duration'),entities=entities,errors=errors,
            cores=[[e['cores'] for e in group] for group in scenario['entities']]))
        print(fmt,index,mirror,'winner',result['winner'],'aborts',dict(aborts),'errors',len(errors),flush=True)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(dict(bundle=current.name,baseline='3024424',builds_sha256=runtime.digest(args.builds),
            engine_sha256=runtime.digest(rt/'generator.jar'),seconds=time.time()-start,rows=rows),ensure_ascii=False,indent=2),encoding='utf-8')
    print('elapsed',time.time()-start)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()

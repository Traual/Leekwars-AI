"""Same state, same calls: isolate evaluator cost from changed search trajectories."""
import argparse
import json
from pathlib import Path
import sys
import integration
import runtime


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--runtime',type=Path,required=True)
    ap.add_argument('--output',type=Path,default=Path('validation/scoring_astra/results/cost.json'))
    args=ap.parse_args();rt=args.runtime.resolve();rows=[]
    for ref in ('3024424',None):
        dest=runtime.bundle(rt,'cost',ref)
        (dest/'Sonde.leek').write_text('''include("includes.leek")
Preload.InitEverything()
ReloadCacheNewTurn()
Preload.InitStateClass()
var S=StateClass.Virtualize()
var start=getOperations()
REPLAN_WEIGHTS=HistoricalWeightsFactory.build(S,S[Me].SIDE)
var contextCost=getOperations()-start
start=getOperations()
var sum=0
for(var i=0;i<100;i++) sum+=ScoringClass.entityValue(S[Me],null)
debug("COST "+jsonEncode(["context":contextCost,"entity":(getOperations()-start)/100,"checksum":sum]))
''',encoding='utf-8')
        (dest/'Idle.leek').write_text('// idle\n',encoding='utf-8')
        sc=integration.scenario(dest,rt);sc['max_turns']=1
        for group in sc['entities']:
            for e in group:
                if not e['ai'].endswith('/Sonde.leek'):e['ai']=f'test/ai/bundles/{dest.name}/Idle.leek'
        path=rt/f'cost-{ref or "current"}.json';path.write_text(json.dumps(sc))
        result=runtime.run(rt,[path],path.with_name(path.stem+'-result.json'))[0]
        errors=[x for x in runtime.logs(result) if len(x)>3 and x[1] in (7,8)]
        lines=runtime.messages(result,'COST ')
        if errors or len(lines)!=1: raise RuntimeError(errors or 'missing cost measurement')
        row=dict(version=ref or 'current',bundle=dest.name,**json.loads(lines[0][5:]))
        rows.append(row);print(row)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(rows,indent=2),encoding='utf-8')


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()

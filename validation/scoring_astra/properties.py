import argparse
import json
from pathlib import Path
import shutil
import sys
import runtime
import integration


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--runtime',type=Path,required=True)
    ap.add_argument('--output',type=Path,default=Path('validation/scoring_astra/results/properties.json'))
    args=ap.parse_args();rt=args.runtime.resolve();dest=runtime.bundle(rt,'properties')
    here=Path(__file__).resolve().parent
    shutil.copyfile(here/'IntegrationProbe.leek',dest/'ScoringProbe.leek')
    shutil.copyfile(here/'PropertiesProbe.leek',dest/'PropertiesProbe.leek')
    (dest/'Sonde.leek').write_text('include("PropertiesProbe.leek")\nPropertiesProbe.play()\n',encoding='utf-8')
    (dest/'Idle.leek').write_text('// idle opponent\n',encoding='utf-8')
    sc=integration.scenario(dest,rt);sc['max_turns']=1
    for group in sc['entities']:
        for e in group:
            if not e['ai'].endswith('/Sonde.leek'):e['ai']=f'test/ai/bundles/{dest.name}/Idle.leek'
    path=rt/'properties.json';path.write_text(json.dumps(sc))
    result=runtime.run(rt,[path],rt/'properties-result.json')[0]
    rows=[json.loads(x[len('PROPERTY '):]) for x in runtime.messages(result,'PROPERTY ')]
    for row in rows: print(row)
    errors=[x for x in runtime.logs(result) if len(x)>3 and x[1] in (7,8)]
    print('errors',errors[:10]);print(runtime.messages(result,'PROPERTY_COST '))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(dict(bundle=dest.name,checks=rows,errors=errors),ensure_ascii=False,indent=2),encoding='utf-8')
    if len(rows)<17 or errors or any(not x['ok'] for x in rows): raise SystemExit(1)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()

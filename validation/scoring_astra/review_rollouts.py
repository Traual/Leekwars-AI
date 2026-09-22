"""Attach observable outcomes to every disagreement, without inferring a cause."""
import argparse
import json
from pathlib import Path
import statistics


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--runtime',required=True,type=Path)
    ap.add_argument('--results',default=Path('validation/scoring_astra/results'),type=Path)
    args=ap.parse_args()
    report=json.loads((args.results/'report.json').read_text(encoding='utf-8'))
    cases=json.loads((args.results/'cases.json').read_text(encoding='utf-8'))
    observations=json.loads((args.results/'observations.json').read_text(encoding='utf-8'))
    raw=[]
    for start in range(0,report['fights'],128):
        raw.extend(json.loads((args.runtime/f'oracle-batch-{start}.json').read_text(encoding='utf-8')))
    assert len(raw)==len(observations)==report['fights']
    output=[]
    for case,row in zip(cases,report['rows']):
        assert case['id']==row['id']
        if row['agrees'] is not False: continue
        alternatives=[]
        for option in (0,1):
            samples=[(obs,r) for obs,r in zip(observations,raw) if obs['case']==case['id'] and obs['option']==option]
            hp=[{e[0]:e[1] for e in r['terminal']} for _,r in samples]
            alternatives.append(dict(option=option,actions=case['config']['options'][option],
                victories=sum(h[0]>0 and h[1]<=0 for h in hp),
                defeats=sum(h[0]<=0 and h[1]>0 for h in hp),
                unresolved=sum(h[0]>0 and h[1]>0 for h in hp),
                final_own_life=statistics.mean(h[0] for h in hp),
                final_enemy_life=statistics.mean(h[1] for h in hp),
                raw_score=statistics.mean(obs['score'][1] for obs,_ in samples),
                placement_penalty=statistics.mean(obs['score'][2] for obs,_ in samples)))
        output.append(dict(id=case['id'],horizon=case['horizon'],score_difference=row['score_difference'],
            oracle_difference=row['oracle_difference'],alternatives=alternatives))
    (args.results/'disagreements.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in output: print(row)


if __name__=='__main__': main()

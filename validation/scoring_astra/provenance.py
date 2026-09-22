"""Record source and executable identity separately; no engine rebuild or mutation."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import runtime


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--engine',required=True,type=Path)
    ap.add_argument('--runtime',required=True,type=Path)
    ap.add_argument('--output',type=Path,default=Path('validation/scoring_astra/results/provenance.json'))
    args=ap.parse_args();engine=args.engine.resolve();rt=args.runtime.resolve();root=runtime.ROOT
    prefix=Path('src/main/java/com/leekwars/generator')
    sources=sorted((engine/prefix/'effect').glob('*.java'))
    sources.extend(engine/prefix/p for p in ['state/Entity.java','state/State.java','state/Team.java',
        'fight/entity/EntityAI.java','attack/EntityState.java'])
    h=hashlib.sha256()
    ai={}
    for f in sorted((root/'New_AI').rglob('*.leek')):
        raw=f.read_bytes()
        h.update(f.relative_to(root/'New_AI').as_posix().encode()+b'\0'+raw)
        ai[f.relative_to(root).as_posix()]=hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()
    report=dict(base_commit=subprocess.check_output(['git','rev-parse','3024424'],cwd=root,text=True).strip(),
        generator_source_commit=subprocess.check_output(['git','-c',f'safe.directory={engine.as_posix()}',
            'rev-parse','HEAD'],cwd=engine,text=True).strip(),
        generator_source_remote='https://github.com/leek-wars/leek-wars-generator.git',
        engine_binaries={f:runtime.digest(rt/f) for f in ['generator.jar','leekscript/leekscript.jar']},
        source_hashes={f.relative_to(engine).as_posix():runtime.digest(f) for f in sources},
        data_hashes={f.relative_to(rt).as_posix():runtime.digest(f) for f in sorted((rt/'data').rglob('*')) if f.is_file()},
        ai_source_lf_sha256=ai,tested_bundle_suffix=h.hexdigest()[:14],python=sys.version.split()[0],
        java=subprocess.run(['java','-version'],capture_output=True,text=True).stderr.strip(),
        note='JARs copied unchanged; generator not rebuilt. Source pin and executable hashes recorded independently.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Bundle',report['tested_bundle_suffix'],'source files',len(sources))


if __name__=='__main__': main()

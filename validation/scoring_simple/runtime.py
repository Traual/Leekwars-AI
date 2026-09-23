"""Isolated, reproducible use of an UNMODIFIED generator. No saved build is edited."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def literal(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, dict):
        return "[" + (", ".join(f"{literal(k)}: {literal(v)}" for k, v in value.items()) or ":") + "]"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(map(literal, value)) + "]"
    return json.dumps(value, ensure_ascii=False)


def setup(engine: Path, runtime: Path):
    if engine.resolve() == runtime.resolve():
        raise ValueError("Use a separate runtime directory; the source generator must stay untouched")
    runtime.mkdir(parents=True, exist_ok=True)
    for file in ("generator.jar", "leekscript/leekscript.jar"):
        target = runtime / file
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or digest(target) != digest(engine / file):
            shutil.copyfile(engine / file, target)
    shutil.copytree(engine / "data", runtime / "data", dirs_exist_ok=True)
    classes = runtime / "classes"
    classes.mkdir(exist_ok=True)
    jars = [runtime / "generator.jar", runtime / "leekscript/leekscript.jar"]
    cp = os.pathsep.join(map(str, [classes, *jars]))
    source = ROOT / "training/v3/fidelite/FideliteRunner.java"
    subprocess.run(["javac", "-encoding", "UTF-8", "-cp", cp, "-d", str(classes), str(source)], check=True)
    return cp


def bundle(runtime: Path, label="simple", ref=None):
    if ref:
        import io
        import tarfile
        raw = subprocess.check_output(["git", "archive", ref, "New_AI"], cwd=ROOT)
        identity = hashlib.sha256(raw).hexdigest()[:14]
        dest = runtime / "test/ai/bundles" / (label + "-" + identity)
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for member in archive.getmembers():
                if member.isfile():
                    target = dest / Path(member.name).relative_to("New_AI")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(member).read())
    else:
        files = sorted((ROOT / "New_AI").rglob("*.leek"))
        h = hashlib.sha256()
        for file in files:
            h.update(file.relative_to(ROOT / "New_AI").as_posix().encode() + b"\0" + file.read_bytes())
        dest = runtime / "test/ai/bundles" / (label + "-" + h.hexdigest()[:14])
        shutil.copytree(ROOT / "New_AI", dest, dirs_exist_ok=True)
    return dest


def run(runtime: Path, paths, output: Path, timeout=180, runner="training.fidelite.FideliteRunner"):
    cp = os.pathsep.join(map(str, [runtime / "classes", runtime / "generator.jar", runtime / "leekscript/leekscript.jar"]))
    proc = subprocess.run(["java", "-Xmx8g", "-cp", cp, runner, *map(str, paths)],
                          cwd=runtime, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".stdout").write_text(proc.stdout, encoding="utf-8")
    output.with_suffix(".stderr").write_text(proc.stderr, encoding="utf-8")
    if proc.returncode:
        raise RuntimeError(f"Java exited with {proc.returncode}; see {output.with_suffix('.stderr')}")
    results = []
    for line in proc.stdout.splitlines():
        if line.startswith("__FIDELITE__\t"):
            result = json.loads(line.split("\t", 2)[2])
            if "runner_error" in result or "exception" in result:
                raise RuntimeError(result)
            results.append(result)
    if len(results) != len(paths):
        raise RuntimeError(f"{len(results)} results for {len(paths)} scenarios; see {output}.stderr")
    output.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    return results


def logs(result):
    for per in result.get("outcome", {}).get("logs", {}).values():
        for entries in per.values():
            yield from entries


def messages(result, prefix):
    return [x[2] for x in logs(result) if len(x) > 2 and isinstance(x[2], str) and x[2].startswith(prefix)]


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', required=True, type=Path)
    parser.add_argument('--runtime', required=True, type=Path)
    args = parser.parse_args()
    setup(args.engine.resolve(), args.runtime.resolve())
    print('Runtime ready; generator and data copied unchanged.')

"""Verify source-only copies without existing app data, model or VM installs."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parent


def copy_sources(destination):
    destination=Path(destination);destination.mkdir()
    for pattern in ('*.py','*.ps1','*.js','*.cjs','*.html','*.css'):
        for source in ROOT.glob(pattern):shutil.copyfile(source,destination/source.name)
    for name in ('config.json','requirements.txt'):shutil.copyfile(ROOT/name,destination/name)
    shutil.copytree(ROOT/'docs',destination/'docs')


def main(ui=False):
    with tempfile.TemporaryDirectory(prefix='clean-',dir=ROOT) as temporary:
        project=Path(temporary)/'source';copy_sources(project)
        assert not any((project/name).exists() for name in ('data','data-demo','models','runtime'))
        result=subprocess.run([sys.executable,'-m','unittest','-q'],cwd=project,timeout=240)
        if result.returncode:return result.returncode
        result=subprocess.run([sys.executable,'verify_system.py'],cwd=project,timeout=90)
        if result.returncode:return result.returncode
        if ui:
            from test_runtime import node_executable
            environment=dict(os.environ,MOA_NODE=node_executable())
            dependency=Path(environment.get('MOA_JSDOM',ROOT/'runtime/ui-tests/node_modules/jsdom')).resolve()
            if not (dependency/'package.json').is_file():raise RuntimeError('UI 검증에는 jsdom 27.4.0이 필요합니다. MOA_JSDOM 또는 개발자 가이드를 확인하세요.')
            environment['MOA_JSDOM']=str(dependency)
            for script in ('verify_ui.py','verify_refinement.py'):
                result=subprocess.run([sys.executable,script],cwd=project,env=environment,timeout=150)
                if result.returncode:return result.returncode
        print('PASS: source-only copy; no installed model/VM/app data; host Python'+(' and external Node/jsdom test dependencies' if ui else ''))
        return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--ui',action='store_true');args=parser.parse_args()
    raise SystemExit(main(args.ui))

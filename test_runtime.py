"""Find a developer Node installation without requiring Codex on user PCs."""
import os
from pathlib import Path
import shutil
import re
import subprocess

NODE_REQUIREMENT='Node.js 20.19+ (20.x), 22.12+ (22.x), 또는 24+'

def compatible_node(candidate):
    try:
        result=subprocess.run([str(candidate),'--version'],stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except (OSError,subprocess.SubprocessError):return False
    match=re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)',result.stdout.strip())
    if result.returncode!=0 or match is None:return False
    major,minor,_=map(int,match.groups())
    return major==20 and minor>=19 or major==22 and minor>=12 or major>=24


def node_executable():
    override = os.environ.get('MOA_NODE')
    if override:
        if not Path(override).is_file():
            raise RuntimeError('MOA_NODE가 가리키는 Node 실행 파일을 찾을 수 없습니다.')
        if not compatible_node(override):raise RuntimeError('MOA_NODE 실행·버전 확인 실패: jsdom 27.4.0에는 '+NODE_REQUIREMENT+'가 필요합니다.')
        return override
    installed = shutil.which('node')
    if installed and Path(installed).is_file() and compatible_node(installed):
        return installed
    try:bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    except RuntimeError:bundled=None
    if bundled is not None and bundled.is_file() and compatible_node(bundled):
        return str(bundled)
    raise RuntimeError('DOM 검증에는 '+NODE_REQUIREMENT+'가 필요합니다. PATH 또는 MOA_NODE를 설정하세요.')

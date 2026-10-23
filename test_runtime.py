"""Find a developer Node installation without requiring Codex on user PCs."""
import os
from pathlib import Path
import shutil


def node_executable():
    override = os.environ.get('MOA_NODE')
    if override:
        if not Path(override).is_file():
            raise RuntimeError('MOA_NODE가 가리키는 Node 실행 파일을 찾을 수 없습니다.')
        return override
    installed = shutil.which('node')
    if installed:
        return installed
    bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    if bundled.is_file():
        return str(bundled)
    raise RuntimeError('DOM 검증에는 Node.js 22 이상이 필요합니다. PATH 또는 MOA_NODE를 설정하세요.')

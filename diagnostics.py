"""Read-only installation checks; no network, model calls, or VM control."""
from pathlib import Path
import shutil
import sys


def inspect_installation(root, config, slack_sdk=False):
    root = Path(root).resolve()
    checks = []
    checks.append({'id': 'python', 'label': 'Python', 'state': 'ready' if sys.version_info >= (3, 11) else 'error', 'detail': '.'.join(map(str, sys.version_info[:3]))})
    for key, label in [('model_file', '로컬 모델 파일'), ('runtime_executable', '모델 실행 도구')]:
        value = config.get(key)
        if not isinstance(value, str) or not value:
            state, detail = 'missing', 'Install.ps1 -DownloadModel로 준비하세요.'
        else:
            path = (root / value).resolve()
            if root not in path.parents:
                state, detail = 'error', '프로젝트 내부 경로를 설정하세요.'
            elif not path.is_file():
                state, detail = 'missing', 'Install.ps1 -DownloadModel로 준비하세요.'
            else:
                state, detail = 'ready', '파일 있음 · 실제 추론과 무결성은 별도 검증'
        checks.append({'id': key, 'label': label, 'state': state, 'detail': detail})
    checks.append({'id': 'slack_sdk', 'label': 'Slack 연결 도구', 'state': 'ready' if slack_sdk else 'optional', 'detail': '설치됨 · 계정 연결은 별도' if slack_sdk else 'Slack 사용 시 Install.ps1로 준비하세요.'})
    checks.append({'id': 'vm_runtime', 'label': '공유 VM 도구', 'state': 'ready' if (root / 'runtime/qemu/qemu-system-x86_64.exe').is_file() else 'optional', 'detail': '선택 사항 · 부팅·가속·화면 성능은 검사하지 않습니다.'})
    try:
        free_gib = round(shutil.disk_usage(root).free / 1024**3, 1)
    except OSError:
        free_gib = None
    return {'version': 1, 'checks': checks, 'disk_free_gib': free_gib, 'scope': '파일 존재와 실행 버전만 확인합니다. 모델·VM·Slack을 실행하거나 연결하지 않습니다.'}

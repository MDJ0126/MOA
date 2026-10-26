# MOA Windows 배포

배포 파일: `dist/MOA-Setup-0.1.0-x64.exe`

Windows 10 이상 x64용 사용자 단위 설치 프로그램입니다. 기본 설치 위치는 `%LOCALAPPDATA%\Programs\MOA`이며, 바탕 화면과 시작 메뉴에 모아 바로가기를 만듭니다. 실행 파일을 설치한 후 첫 실행의 **준비 시작**을 누르면 무료 AI 모델과 선택한 VM 환경을 다운로드합니다. Slack 등 개인 서비스 인증은 설치 후 연결합니다.

Python과 Python 라이브러리, 관리 창 실행 파일은 포함됩니다. AI 모델, VM, Microsoft 시스템 런타임 다운로드에는 인터넷이 필요합니다. 설치 파일은 현재 코드 서명되지 않았습니다. 제거할 때 업무 데이터와 다운로드한 모델은 보존합니다.

## 빌드

`tools/build_release.py`를 Python으로 실행하여 깨끗한 배포 폴더를 생성한 뒤 아래 명령을 실행합니다. 빌드 입력은 `runtime/build-tools`의 Python 3.13.9 embedded ZIP, dependency wheels, NSIS 3.11과 프로젝트 런타임 에셋입니다.

```powershell
python tools/build_release.py
& runtime/build-tools/nsis-3.11/makensis.exe /V2 packaging/MOA.nsi
Get-FileHash dist/MOA-Setup-0.1.0-x64.exe -Algorithm SHA256
```

`release-manifest.json`에는 배포 파일별 SHA256이 기록됩니다. 소스 디렉터리 전체를 압축하지 않고 허용한 파일만 포함하여 개인 설정과 데이터가 섞이지 않도록 합니다.

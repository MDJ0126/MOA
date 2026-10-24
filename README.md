<p align="center">
  <img src="docs/assets/moa-logo.png" alt="모아 — 코럴색으로 이어지는 에이전트 팀 로고" width="96">
</p>
<h1 align="center">모아 · MOA</h1>
<p align="center"><strong>작은 팀이 차례대로, 당신의 일을 이어갑니다.</strong></p>
<p align="center">내 PC에서 실행하는 로컬 AI 에이전트 팀</p>
<p align="center">
  <a href="#빠른-시작">빠른 시작</a> &middot;
  <a href="docs/README.md">문서</a> &middot;
  <a href="docs/development.md">개발·검증 안내</a>
</p>

---

할 일과 완료 조건을 입력하면 **계획 → 개발 → 검증 → 보고**를 순서대로 진행합니다. 팀원들이 로컬 모델 하나를 공유하고, 대화와 결과물은 PC에 저장합니다.

## 빠른 시작

**Windows x64 · Python 3.11 이상**이 필요합니다. 저장소를 내려받은 폴더에서 PowerShell로 실행해 주세요.

모델 설치 없이 먼저 체험하려면:

```powershell
.\Start.ps1 -Demo
```

[체험 화면 열기](http://127.0.0.1:8767) → 팀 구성 저장 → 할 일 맡기기 → 결과 확인.
데모는 샘플 결과로 흐름을 보여주며, 실제 AI·Slack·VM에 연결하지 않습니다.

실제 로컬 AI를 사용하려면:

```powershell
.\Install.ps1 -DownloadModel
.\Start.ps1
```

[워크스페이스 열기](http://127.0.0.1:8765)에서 시작하세요. 기본 모델은 Qwen2.5-Coder-7B-Instruct Q4_K_M이며, 모델 다운로드에 약 4.68GB가 필요합니다. CPU로 한 번에 하나의 추론을 실행합니다.

종료는 `.\Stop.ps1`, 데모 종료는 `.\Stop.ps1 -Port 8767`입니다.

## 할 수 있는 일

- **내 팀 구성** — 1~8명, 이름·역할·추가 지침 설정
- **작업과 대화** — 담당자별 진행 상황, 내부 인계와 기록 확인
- **검증과 결과물** — 필수 파일·Python 테스트 확인, 실패 시 설정 한도 내 수정, 보고서·ZIP 다운로드
- **선택 연결** — Slack 지시·보고, 팀이 순서대로 사용하는 공유 VM

## 문서

[문서 모음](docs/README.md)에서 시작·팀 구성·모델·Slack·VM·백업·문제 해결 안내를 확인하실 수 있습니다. 실행 중에는 앱의 **사용 가이드**에서 스크린샷과 함께 읽으실 수 있습니다.

개발 환경 구성과 검증 명령, 상세 동작은 [개발·검증 안내](docs/development.md)에 정리했습니다.

## 현재 범위

실제 코드 실행 검증은 **Python**을 대상으로 합니다. C#·C++ 컴파일러와 게임 엔진 검증은 연결하지 않았으며, 기본 모델은 비전 모델이 아닙니다. Slack은 계정 설정 후 연결해야 합니다.

Python 테스트는 현재 사용자 권한으로 실행되며 OS 보안 샌드박스가 아닙니다. VM·Slack의 실제 사용 범위와 운영 제한은 [상세 안내](docs/development.md)를 확인해 주세요.

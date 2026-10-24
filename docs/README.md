# 모아 문서

[프로젝트 소개](../README.md) · [개발·검증 안내](development.md)

아래 문서는 GitHub에서 바로 읽으실 수 있습니다. 앱에서도 **사용 가이드**에서 같은 내용을 확인하실 수 있습니다.

| 안내 | 내용 |
| --- | --- |
| [처음 시작하기](guides/getting-started.md) | 설치, 데모 체험, 첫 작업 |
| [에이전트 팀 구성](guides/team.md) | 팀원, 역할, 추천 프리셋 |
| [작업과 결과 확인](guides/tasks.md) | 완료 조건, 대화, 보고서와 ZIP |
| [내 PC와 로컬 모델](guides/models.md) | 메모리, 모델 설치, 실행 방식 |
| [Slack 연결](guides/slack.md) | 봇 준비, 지시와 보고 |
| [공유 VM 작업 환경](guides/environment.md) | 설치, 화면 공유, 실행 범위 |
| [실행과 복구](guides/operations.md) | 중지, 재개, 운영 |
| [문제 해결](guides/troubleshooting.md) | 연결 오류와 실패 확인 |
| [데이터와 저장 위치](guides/privacy.md) | 백업, 복원, 토큰, 공유 범위 |

앱 실행 후 일반 모드는 `http://127.0.0.1:8765/#docs`, 데모는 `http://127.0.0.1:8767/#docs`를 브라우저 주소창에 입력하세요. 로컬 주소는 앱이 실행 중인 PC에서만 열립니다.

## 개발과 검증

- [개발·검증 안내](development.md) — 실행 명령, 저장 구조, 복구와 제한
- [테스트 실행 안내](../tests/README.md) — Python·DOM 테스트 위치와 실행 명령
- [개선 검증 기준](development/REFINEMENT.md)
- [공유 VM 검증 기준](development/VM_GOAL.md)
- [화면 비조작 검증 기준](development/NON_SCREEN_GOAL.md)
- [지속 고도화 기록](development/CONTINUOUS_GOAL.md)

앱의 HTML 가이드를 수정한 뒤에는 저장소 루트에서 `node tools/export_guides.cjs`를 실행하면 GitHub용 문서 9개가 갱신됩니다. Node·jsdom 준비 방법은 [개발·검증 안내](development.md)를 확인해 주세요.

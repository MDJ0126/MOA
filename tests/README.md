# 테스트

저장소 루트에서 실행합니다.

```powershell
python -m unittest -q
python verify_system.py
python verify_ui.py
python verify_refinement.py
```

단일 모듈은 `python -m unittest tests.test_app -v`처럼 지정합니다.
Python 테스트와 DOM 검증용 `test_*.cjs`를 이 폴더에 모았습니다. DOM 검증 실행기는 루트에 있으며 Node·jsdom 준비 방법은 [개발·검증 안내](../docs/development.md)를 확인해 주세요.

실제 모델·VM·Slack 검증은 위 명령의 통과와 별도로 구분합니다.

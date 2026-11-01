# 설치 환경과 검증 범위

기본 설치는 `Frontend/scripts/setup-python.mjs`와 `Backend/scripts/setup-agent-demo.sh` 모두
`Backend/requirements-agent-lock.txt`를 사용한다. `requirements-agent.txt`만 수정해도
기본 설치 버전이 바뀌는 것은 아니다. 의존성 변경 시 두 파일의 버전을 함께 확인한다.

| 용도 | 설치 목록 | 범위 |
|---|---|---|
| 기본 Python 워커·회귀 테스트 | `Backend/requirements-agent-lock.txt` | 실행 의존성과 pytest의 검증 버전 |
| 통합 규격 검사 | `Backend/requirements-local-integration.txt` | 위 잠금 목록과 jsonschema |
| 선택 SVM 추천 | `Backend/integrations/tool_recommender_reference/requirements-ml-reference.txt` | numpy/scipy/pandas/scikit-learn/joblib |
| 문서 변환 | `Backend/requirements-parser.txt` | 별도 `.venv-docling` 환경; 모델 가중치는 별도 준비 |
| 프런트엔드·서버 | `Frontend/package-lock.json` | `npm ci`로 설치 |

저장소 루트에서 기본 환경과 통합 검사 의존성을 설치하는 명령:

```sh
npm --prefix Frontend ci
npm --prefix Frontend run setup
Backend/.venv-agent/bin/python -m pip install -r Backend/requirements-local-integration.txt
```

Windows에서는 Python 경로를 `Backend/.venv-agent/Scripts/python.exe`로 바꾼다.
SVM 사용 시에만 해당 목록을 워커 환경에 추가하고, 파서는 별도 환경을 사용한다.
런타임 동작 검증과 새 환경 설치 검증은 구분해 기록한다.

버전을 일괄 업그레이드하거나 로컬 전체 `pip freeze`로 기존 목록을 덮어쓰지 않는다.
선택 의존성이 필요한 검사와 모델 호출 없이 가능한 회귀 검사를 구분한다.
`jsonschema`는 통합 규격 검사 의존성이며, 기본 워커 회귀 검사 통과만으로
이 선택 의존성까지 설치됐다고 판단하지 않는다.

`.gitignore`는 가상환경·실제 환경설정·DB·업로드·결과 파일을 제외한다.
테스트, 공개 예제, 호환 모듈, 참조 스냅샷은 업로드 대상이다.
이미 추적 중인 파일은 제외 규칙을 추가해도 자동으로 추적 해제되지 않는다.

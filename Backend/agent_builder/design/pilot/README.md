# 워크플로우 전처리 코드·명세

사용자 요청을 워크플로우 초안 또는 확인 질문으로 변환하는 합성 파일럿입니다. 이 폴더는 전처리 코드와 제안 명세만 보관합니다.

| 파일 | 역할 |
| --- | --- |
| `build_dataset.py` | 공백·중복 정제, 구조 검증, 시나리오별 데이터 분할과 모의 실행 |
| `contract.json` | 워크플로우 초안의 제안 규약 v0.1 |
| `tool_registry.json` | 합성 예제에 사용하는 도구 명세 |

합성 예제와 출처·검증 범위는 [docs/examples/workflow_preprocessing](../../../docs/examples/workflow_preprocessing/README.md)에 있습니다. 실제 앱 데이터는 프로젝트 루트의 `data/` 또는 `EASYFLOW_DATA_DIR`에서 관리합니다.

프로젝트 루트에서 재현합니다. Python 표준 라이브러리만 사용합니다.

```bash
python3 Backend/integrations/workflow_preprocessing/build_dataset.py
python3 Backend/integrations/workflow_preprocessing/build_dataset.py --output /tmp/a4b-preprocessing
```

기본 결과 폴더는 `docs/examples/workflow_preprocessing/`입니다. 검수 CSV, 통계, 보고서와 명세 사본을 결과 폴더에 생성하고 ZIP은 그 상위 폴더에 저장합니다. 생성 안내는 `DATASET_NOTES.md`를 사용하여 README와 이 폴더의 코드·명세를 유지합니다. `--output`으로 `Backend/integrations/` 내부를 지정할 수 없습니다.

합성 예제를 별도 SQLite 테이블에 가져오려면 Python 에이전트 환경 설치 후 실행합니다.

```bash
Backend/.venv-agent/bin/python Backend/scripts/import-preprocessing.py
# 별도 결과 또는 데이터 저장 폴더를 지정하려면:
Backend/.venv-agent/bin/python Backend/scripts/import-preprocessing.py --source /tmp/a4b-preprocessing --directory /tmp/a4b-import
```

기본 저장 폴더는 `EASYFLOW_DATA_DIR` 또는 프로젝트 루트의 `data/`입니다. 환경변수, `--directory`, `--source`의 상대 경로는 프로젝트 루트에서 해석하므로 다른 폴더에서 실행해도 같은 위치를 사용합니다. 예제를 가져와도 사용자의 실행 워크플로우가 자동 생성되지 않습니다. 30개 합성 사례는 사람 검수와 백엔드 명세 대조가 미완료이며 모델 학습·실제 외부 연동 성능을 보여주는 자료가 아닙니다.

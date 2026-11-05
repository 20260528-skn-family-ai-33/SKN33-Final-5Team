# SVM 도구 추천 참고 연동안

설치·활성 상태 진단: [팀 모델 전달·설치 안내](../../../docs/svm-deployment.md).
저장소 루트에서 `npm run ml:install -- --source "/path/to/bundle"`,
`npm run ml:check -- --probe`로 설치와 실제 추천 연결을 구분해 확인합니다.
기준 모델과 카탈로그는 `runtime/ml-bundle`에 포함되어 Git clone으로 받을 수 있습니다.
원본 팀 공유 ZIP은 손상된 파일 복구용이며, 두 파일의 고정 해시와 줄바꿈 원본을 유지합니다.

기본값은 A4B_TOOL_ADVICE_MODE=hint, A4B_TOOL_ADVICE_BACKEND=svm56입니다. 명시적으로 A4B_TOOL_ADVICE_MODE=off를 지정한 경우에만 추천을 끕니다. 로컬 기본 파일 위치는 저장소의 runtime/ml-bundle/model/recommender_char_svm_5431.joblib 및 runtime/ml-bundle/catalog/catalog_56.json이며 실행 디렉터리에 의존하지 않습니다. 별도 후보를 사용할 때만 A4B_SVM_MODEL_PATH와 A4B_SVM_CATALOG_PATH에 신뢰 가능한 파일을 지정합니다. 모델과 카탈로그 해시는 코드에 고정되어 있으며 서비스 추론에 원본·합성 학습 데이터는 필요하지 않습니다. joblib는 신뢰할 수 있는 파일만 사용해야 합니다.

이 코드는 56개 도구를 서비스 실행에 등록하지 않으며 등록된 도구에 대해서만 참고 후보를 반환합니다. 전체 도구 선택 경로를 유지해야 합니다. SVM margin은 확률이 아닙니다. 기준 패키지는 `A4B_ML_Qwen_전체작업자료_2026-10-31/08_서비스연동패키지`이며 고정 모델의 `reply_review`·`review_summary`는 양성 0건으로 미학습입니다.

로컬 의존성 설치·갱신: 저장소 루트에서 `npm run setup` (기존 환경도 명시적으로 실행하면 ML 의존성을 설치합니다).

로컬 검사: 저장소 루트에서 `python -m pytest Backend/tests/ml -q`.

실제 고정 모델 진단: `Backend/`에서 `python -m ml.svm56.verify_service --bundle /절대경로/08_서비스연동패키지`. 5개 진단의 성공 여부와 추천 보류·규칙 출처를 JSON으로 출력합니다. 소수 회귀 사례이며 독립 정확도 평가나 실제 워크플로우 실행이 아닙니다.

단일 요청으로 학습된 모델에 대화 전체를 이어 붙이지 않습니다. 여러 사용자 발언 또는 명시적 부정·취소가 있으면 추천을 보류하고 전체 도구를 보는 기존 설계 경로를 유지합니다. 이 동작은 부정문 이해 능력을 학습한 것이 아닙니다.

부정 보류는 저장·발송 등 작업 지시에 연결된 표현에 한정합니다. "승인 없이 공개하는지 검수"의 자료 내용이나 따옴표 안의 검색 대상은 작업 취소로 처리하지 않습니다. 규칙은 일반적인 자연어 이해를 대체하지 않으며 여러 사용자 발언은 계속 설계 단계로 넘깁니다.

서비스 보완 후보는 `source=service_contract`와 이유를 표시하며 SVM 점수를 만들지 않습니다. `source=svm56`만 실제 모델 점수를 가집니다. 자료 종류에 따라 표 입력과 문서 입력을 구분하고, 표 처리 후 명시적 파일 저장에는 `write_result(table)`을 제안합니다. `export_result`의 이름을 바꿔 연결하지 않습니다. 규칙은 일부 명시적 표현만 처리하며 워크플로우 검증·검수·승인을 대체하지 않습니다.

첨부 문서가 있는 요약·추출·검색·응대 검수 후보에는 필요한 문서 읽기 후보를 보완합니다. 새 응대 검수와 기존 검수 결과 보고서를 구분하며, 새 검수 후 보고서를 요청하면 두 작업을 모두 제안합니다. 검수 보고서는 원래 첨부 형식과 관계없이 `review_summary`의 table 출력에 맞춰 `write_result`를 제안합니다. 별도 원문·요약을 함께 저장하는 모호한 요청은 한 writer로 합치지 않습니다. 이 후보 목록은 실행 그래프가 아니며 연결·승인 검사는 기존 엔진이 수행합니다.

자료 유형은 실행기가 쓰는 `documents.resources.document_resources.supports`로 판단합니다. CSV·XLSX가 검증된 파서 본문을 함께 제공하면 표와 문서 읽기를 모두 지원하는 것으로 전달합니다. 파일명 언급이나 capabilities 문자열만으로 기능을 만들지 않습니다. 표/문서 양쪽 읽기가 가능해도 처리 도구의 실제 결과가 table로 확인되고 다른 결과 생산 후보가 없으면 표 저장 도구를 보완합니다.

Docker는 기본으로 ML 의존성과 SVM 힌트를 켜고 기준 모델 두 파일을 `/app/runtime/ml-bundle`에 포함합니다. 기본 빌드는 실제 추천 probe를 통과해야 성공합니다. EC2는 추가 모델 마운트 없이 이 경로를 사용하며 배포 전 현재 운영 설정으로 한 번 더 검사합니다. 모델 폴더 변경은 GitHub Actions에서 백엔드 변경으로 인식합니다. 모델·해시·의존성·경로 오류는 성공으로 취급하지 않으며 기존 앱 교체 전에 배포를 차단합니다.

로컬 Docker Compose는 기존처럼 `A4B_ML_BUNDLE_DIR`(기본 `./runtime/ml-bundle`)을 `/app/ml-bundle`에 읽기 전용으로 연결하므로 별도 후보를 시험할 수 있습니다. `.env`의 `off`, `A4B_WITH_ML=0`, 명시적 모델 경로는 기본값보다 우선합니다. 운영 자동배포의 SVM 검증을 통과하려면 실제 환경이 `hint / svm56`이며 올바른 파일과 ML 의존성을 제공해야 합니다.

합성 보강·후보 학습과 평가 한계는 [학습 안내](../training/README.md)를 참고하세요. 후보 모델을 배포하려면 독립 평가와 고정 해시의 명시적 갱신이 필요하며 학습 스크립트가 자동 교체하지 않습니다.

평가한 후보를 로컬에서 명시적으로 선택하려면 `A4B_SVM_MODEL_PATH`와 함께 `A4B_SVM_EXPECTED_MODEL_SHA256`에 해당 후보의 `RESULTS.json`에 기록된 정확한 해시를 넣습니다. 지정하지 않으면 기존 기준 모델 해시를 사용합니다. 카탈로그 해시·라벨 순서는 계속 고정 검증합니다. 도구별 기준값이 있는 모델은 그 기준값으로 추천하며 margin과 기준값을 함께 전달합니다. 이를 확률로 해석하지 않습니다.

후보 연결 진단은 `python -m ml.svm56.verify_service --bundle /path/to/baseline-package --model /path/to/candidate.joblib --model-sha256 <exact-sha256>`입니다. Docker에서 후보 파일 이름이 다르면 `A4B_DOCKER_SVM_MODEL_PATH`를 읽기 전용 bundle 안의 컨테이너 경로로 지정합니다. 기존 모델의 기본 설정이나 원본 가중치를 자동 덮어쓰지 않습니다.

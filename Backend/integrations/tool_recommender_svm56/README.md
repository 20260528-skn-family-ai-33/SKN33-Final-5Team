# SVM 도구 추천 참고 연동안

기본값은 off이며 기존 legacy 동작을 보존합니다. A4B_TOOL_ADVICE_MODE=hint 및 A4B_TOOL_ADVICE_BACKEND=svm56을 모두 지정한 경우에만 SVM 후보를 사용합니다. A4B_SVM_MODEL_PATH와 A4B_SVM_CATALOG_PATH에 신뢰 가능한 로컬 파일을 지정하세요. 본 브랜치에는 모델 파일, 원본·합성 데이터, 카탈로그를 게시하지 않습니다. 파일은 별도 전달한 팀 공유 ZIP에서 확보하며 모델과 카탈로그 해시가 코드에 고정되어 있습니다. joblib는 신뢰할 수 있는 파일만 사용해야 합니다.

이 코드는 56개 도구를 서비스 실행에 등록하지 않으며 등록된 도구에 대해서만 참고 후보를 반환합니다. 전체 도구 선택 경로를 유지해야 합니다. SVM margin은 확률이 아닙니다. 검수 도구는 소수 합성 사례에서 누락이 커 결과에 의존할 수 없습니다. 모델 파일, 라이브러리 버전 및 운영 연동은 팀에서 확인해야 합니다.

로컬 검사: Backend 폴더에서 python -m unittest discover -s integrations/tool_recommender_svm56/tests -v. 실제 워크플로 실행이나 서비스 배포는 검증하지 않았습니다.

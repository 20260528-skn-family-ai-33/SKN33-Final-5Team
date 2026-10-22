A4B 파서 출력 JSON 규격 초안 v0.1
2026-10-20

1. index.html을 브라우저로 열어 필드·분담·DB 매핑을 검토하세요.
2. 삼성67페이지 실제 값은 samsung-p67.parser-result.json에 있습니다. 포맷은 새 어댑터 제안입니다.
3. parser-result.schema.json은 파서 API용 JSON Schema입니다. DB 업무 추출 규격 카탈로그에 그대로 등록하지 마세요.
4. chunk-example과db-projection은 설명용이며 현재 서비스 청킹 출력이나 INSERT 가능한 완성 행이 아닙니다.
5. python -m pip install jsonschema 후 python validate_examples.py로 로컬 검증할 수 있습니다. DB/네트워크 쓰기는 없습니다.
6. 원본 전체87페이지 중67페이지만 포함하므로 전체 문서 ready로 취급하지 마세요.
7. 현재 서비스 코드를 수정하거나 AWS/RDS/S3에 적용하지 않았습니다.
8. 첫 작은 표의 헤더 표시 등 알려진 문제는 processing.warnings를 확인하세요.

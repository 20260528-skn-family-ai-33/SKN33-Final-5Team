# A4B EC2 자동 배포

`main`에 PR이 병합되거나 커밋이 푸시되면 기존 EC2에 자동으로 배포합니다. 병합 전에 작업 브랜치를 확인하려면 먼저 해당 브랜치를 이 저장소에 푸시한 뒤 **Actions → Deploy A4B to EC2 → Run workflow**를 엽니다. **Use workflow from**은 `main`으로 두고, `branch` 입력란에 테스트할 브랜치 이름을 넣습니다.

테스트 브랜치도 같은 EC2와 공개 HTTPS 주소에 배포됩니다. 팀원 모두 마지막으로 배포에 성공한 버전을 보게 됩니다. 테스트 직전 버전으로 돌아가려면 `operation=restore`를 실행합니다. 최신 `main`을 배포하려면 `operation=deploy`, `branch=main`으로 실행합니다. 테스트 중에 새 `main` 배포가 성공해도 운영 버전은 `main`으로 바뀝니다. 컨테이너를 교체하는 동안 접속이 잠시 끊길 수 있고, 테스트 중 운영 데이터에 생긴 변경은 복귀 후에도 남습니다.

새 커밋의 테스트와 이미지 빌드는 AWS 권한이 없는 작업에서 수행합니다. 배포 작업은 선택한 이미지를 ECR에 올리고, `main`의 배포 스크립트로 EC2에서 해당 구성요소를 교체합니다.

GitHub에 `production` 환경을 만들고 워크플로우 실행 브랜치를 `main`으로 제한합니다. 운영 권한으로 실행해도 되는 팀 작업 브랜치만 선택합니다. 자동 배포와 수동 배포 모두 아래 변수를 사용하고, 같은 서버에서 동시에 실행되지 않도록 직렬화합니다.

| 변수 | 값 |
| --- | --- |
| `A4B_DEPLOY_ROLE_ARN` | GitHub Actions가 맡을 AWS 배포 역할 ARN |
| `A4B_ECR_REPOSITORY` | 서울 리전의 A4B ECR 저장소 URI. 이미지 태그는 붙이지 않습니다. |
| `A4B_EC2_INSTANCE_ID` | 기존 A4B EC2 인스턴스 ID |
| `A4B_PUBLIC_URL` | 기존 공개 HTTPS 주소. 끝에 슬래시를 붙이지 않습니다. |

AWS 배포 역할은 이 저장소의 `production` 환경 OIDC 토큰만 신뢰합니다. `sub`는 `repo:20260528-skn-family-ai-33@288865373/SKN33-Final-5Team@1391548150:environment:production`입니다. 역할에는 지정한 ECR 저장소에 이미지를 올리고, 지정한 EC2에 `AWS-RunShellScript`를 실행하고 결과를 조회할 권한이 필요합니다. EC2를 SSM 관리 노드로 등록하고, 인스턴스 역할에 해당 ECR 이미지 읽기 권한과 기존 앱 권한을 부여합니다.

교체 스크립트는 기존 `a4b-jhd-aws`의 환경 변수와 두 마운트를 재사용합니다. 따라서 테스트 중에도 기존 Cognito·RDS·S3 설정과 데이터에 연결됩니다.

새 이미지가 시작된 뒤 EC2의 앱 화면과 공개 `/api/health`를 확인하고, 실패하면 이전 컨테이너를 다시 시작합니다. ECR에서 새 이미지를 받으면서 이전 버전도 보관할 디스크 공간을 확인한 뒤 첫 배포를 실행합니다.

## 서비스 분리 배포

운영 전환은 Docker Compose 2.30 이상, 가용 메모리 2GiB 이상, 여유 디스크 6GiB 이상이 필요합니다. 운영과 같은 제한에서 CPU OCR의 메모리와 레이아웃·텍스트 인식 결과를 확인하고 전환합니다. 전환 시 `branch=main`, `target=all`을 사용합니다.

- `frontend`는 빌드한 정적 파일을 EC2에 버전별로 보관하고 연결을 교체합니다. 기존 Nginx 역방향 프록시와 앱의 정적 파일 응답 경로를 사용합니다.
- `backend`는 Node.js와 Python 워커 이미지입니다. 기존 RDS CA와 데이터 볼륨을 연결하고, 기존 EC2 역할을 사용합니다.
- `ocr`는 CPU PaddleOCR 이미지입니다. 앱은 RunPod GPU 파서를 우선 사용하고, 기존 대체 처리 조건에 해당하면 EC2 루프백 주소의 OCR 컨테이너를 호출합니다. 외부에서 접근할 포트는 열지 않습니다.

`target=auto`는 각 영역에 실제 배포된 커밋과 대상 커밋을 비교합니다. `frontend`, `backend`, `ocr`, `all`로 범위를 직접 선택할 수도 있습니다. 기존에 검증한 동일 커밋의 영역별 ECR 이미지가 있으면 재사용하고, 새 이미지는 BuildKit 캐시를 활용합니다. `operation=restore`는 테스트 시작 직전의 서비스 버전과 프론트 파일을 복구합니다.

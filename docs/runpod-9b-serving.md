# 두 9B 모델만 자동 로딩하기

이미 준비된 `/workspace/a4b` 네트워크 디스크 배포에 적용한다. 모델 생성·등록이나
새 GPU 임대는 수행하지 않는다. 모델 가중치, 등록 영수증, 주소, 인증키와 파서 설정은 보존한다.

| 역할 | 자동 로딩 모델 |
| --- | --- |
| 지원 판단 | `a4b-qwen35-9b-previous-pilot32k` |
| 업무 설계 | `a4b-qwen35-9b-final664-pilot32k` |

`start-final.sh`는 내부 Ollama(11435), GPU 파서(8100)를 시작하고 준비 검사와
32,768 문맥 GPU 로딩을 통과하면 기존 인증 게이트웨이(11434)를 연다.
`final_model_profile.py`가 두 필수 모델을 지정한다. 기존 `models-registered.json`에
4B 기록이 남아 있어도 준비 검사·워밍업 대상에는 포함하지 않는다. 필수 9B의
등록 식별값이 없거나 다르면 시작에 실패한다. 로딩 이후에도 식별값과 GPU 적재를 검사한다.

## 기존 서버에 적용

1. AWS의 기존 `/var/lib/a4b-deploy/env-overrides`를 백업하고
   `A4B_PLANNER_MODE=deepagent`, `A4B_VERIFIER_MODE=off` 두 값을 반영한다.
   기존 9B 모델·이전 모델 별칭·주소·인증·DB·요구조건·도구 선택 설정은 보존한다.
   root 소유 및 권한 600을 유지하고 GitHub Actions로 승인한 main을 배포한다.
2. RunPod의 현재 `start-final.sh`, `warm-final.py`, `check-final-ready.py`를 백업한다.
   이 세 파일과 `final_model_profile.py`를 `/workspace/a4b/app/scripts/runpod/`에
   임시 이름으로 올리고 검증 후 원자적으로 교체한다. 실행 중인 셸 파일에 덮어쓰지 않는다.
3. 진행 중인 요청이 없는지 확인하고 기존 시작 감독 프로세스에 TERM을 보내 자식 서비스를
   정리한 뒤 새 `start-final.sh`를 실행한다. 서비스가 잠시 중단된다.
   **Pod 자체를 Stop하지 않는다.** GPU 할당과 네트워크 디스크를 그대로 유지한다.
4. `/run/a4b-final/ready`, `/workspace/a4b/logs/warmup-latest.json`, 내부 `/api/ps`를
   확인한다. 두 9B만 로딩돼 있어야 하며 파서와 공개 인증 경로도 정상이어야 한다.
5. 운영 컨테이너의 새 대화로 실제 설계·확인·저장·실행을 검사한다. 테스트 데이터는
   별도 저장소에 두고 호출 모델과 DeepAgent 경로, 결과 파일, 4B 호출 부재를 확인한다.

기존 대화에 `verification_required` 또는 `verification_enforced`가 있으면 환경변수가
`off`여도 필수 검토가 유지된다. 이 변경은 기존 대화나 대기 검토를 자동 수정하지 않는다.
4B 모델 파일도 삭제하지 않으며 명시적 요청이 오면 다시 로딩될 수 있다.
자동 로딩 제외와 모든 4B 요청의 강제 차단은 다른 동작이다.

실패하면 백업한 RunPod 파일을 복원하고 서비스만 재시작한다. AWS도 저장한 override와
이전 컨테이너로 복구한다. 기존 등록 영수증을 바꾸지 않아 이전 세 모델 구성으로 복구할 수 있다.

## 자동 검사

```sh
python -m pytest Backend/tests/deployment/test_runpod_final_models.py -q
bash -n scripts/runpod/start-final.sh
```

세 모델 영수증에서 두 모델만 선택하는지, 4B 요청이 생기지 않는지, 누락·잘못된 식별값과
예상 밖 GPU 모델을 거부하는지, 파서가 준비되지 않으면 시작을 차단하는지 검사한다.

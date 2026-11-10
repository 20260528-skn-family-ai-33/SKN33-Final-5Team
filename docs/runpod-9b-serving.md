# 두 9B 모델만 자동 로딩하기

이미 준비된 `/workspace/a4b` 네트워크 디스크 배포에 적용한다. 모델 생성·등록이나
새 GPU 임대는 수행하지 않는다. 모델 가중치, 등록 영수증, 주소, 인증키와 파서 설정은 보존한다.

| 역할 | 자동 로딩 모델 |
| --- | --- |
| 지원 판단 | `a4b-qwen35-9b-previous-pilot32k` |
| 업무 설계 | `a4b-qwen35-9b-final664-pilot32k` |

`start-final.sh`는 내부 Ollama(11435), GPU 파서(8100)를 시작하고 준비 검사와
32,768 문맥 GPU 로딩을 통과하면 기존 인증 게이트웨이(11434)를 연다.
시작 락을 얻은 직후 이전 준비 표시를 지우며, `check-gateway-ready.py`로 인증된
응답과 두 모델의 식별값을 확인한 뒤에만 `/run/a4b-final/ready`를 기록한다.
워밍업도 감독 프로세스가 추적하는 별도 프로세스 그룹에서 실행해, TERM 수신 시
모델 응답의 900초 제한을 기다리지 않고 다른 서비스와 함께 종료한다.
`final_model_profile.py`가 두 필수 모델을 지정한다. 기존 `models-registered.json`에
4B 기록이 남아 있어도 준비 검사·워밍업 대상에는 포함하지 않는다. 필수 9B의
등록 식별값이 없거나 다르면 시작에 실패한다. 로딩 이후에도 식별값과 GPU 적재를 검사한다.

## 기존 서버에 적용

1. AWS의 기존 `/var/lib/a4b-deploy/env-overrides`를 백업하고
   `A4B_PLANNER_MODE=deepagent`, `A4B_VERIFIER_MODE=off` 두 값을 반영한다.
   기존 9B 모델·이전 모델 별칭·주소·인증·DB·요구조건·도구 선택 설정은 보존한다.
   root 소유 및 권한 600을 유지하고 GitHub Actions로 승인한 main을 배포한다.
2. RunPod의 현재 `start-final.sh`, `warm-final.py`, `check-final-ready.py`를 백업한다.
   이 세 파일과 `final_model_profile.py`, `check-gateway-ready.py`를 `/workspace/a4b/app/scripts/runpod/`에
   임시 이름으로 올리고 검증 후 원자적으로 교체한다. 실행 중인 셸 파일에 덮어쓰지 않는다.
3. 진행 중인 요청이 없는지 확인하고 기존 시작 감독 프로세스에 TERM을 보내 자식 서비스를
   정리한 뒤 새 `start-final.sh`를 실행한다. 서비스가 잠시 중단된다.
   **Pod 자체를 Stop하지 않는다.** GPU 할당과 네트워크 디스크를 그대로 유지한다.
4. `/run/a4b-final/ready`, `/workspace/a4b/logs/warmup-latest.json`, 내부 `/api/ps`를
   확인한다. 두 9B만 로딩돼 있어야 하며 파서와 공개 인증 경로도 정상이어야 한다.
5. 운영 컨테이너의 새 대화와 기존 대화 사본으로 실제 설계·확인·저장·실행을 검사한다. 테스트 데이터는
   별도 저장소에 두고 호출 모델과 DeepAgent 경로, 결과 파일, 4B 호출 부재를 확인한다.

`A4B_VERIFIER_MODE=off`는 기존 대화의 `verification_required`와
`verification_enforced`보다 우선한다. 대화가 다시 실행될 때 보조 검토의 활성 표시와
경로를 해제하고, 기존 검토가 활성 상태였거나 검토 오류 표시가 남은 초안은 기본 검증을
다시 수행해 현재 오류와 상태를 계산한다. 검토 노드를 거치지 않고 판단·확인·저장 단계에서
재개하거나 이전 off 코드가 검토 표시만 해제한 경우도 포함한다. 실제 연결·조건 오류는 유지한다.
과거 메시지·검토 결과·저장 워크플로우와 버전은 삭제하지 않으며 DB 일괄 수정도 필요 없다.
기본 설정 검증, 업무 조건 검증, 사용자 확인은 계속 적용한다.

대기 검토 실행기도 `off`에서는 기존 대기 기록을 처리하지 않고 그대로 보존한다.
검토를 다시 켜려면 서버 설정을 `shadow`, `defer`, `enforce` 중 하나로 명시적으로 변경해야 한다.
4B 모델 파일과 범용 모델 API는 유지하므로 외부에서 직접 보내는 4B 요청까지 차단하지는 않는다.
예전 대화·워크플로우의 주 모델 식별자 `a4b-qwen35-4b-16k`는 기존 서버 별칭 설정에 의해
final664 9B로 해석된다. 이 별칭은 유지한다.

실패하면 백업한 RunPod 파일을 복원하고 서비스만 재시작한다. AWS도 저장한 override와
이전 컨테이너로 복구한다. 기존 등록 영수증을 바꾸지 않아 이전 세 모델 구성으로 복구할 수 있다.

## 자동 검사

```sh
python -m pytest Backend/tests/deployment/test_runpod_final_models.py Backend/tests/deployment/test_runpod_supervisor.py Backend/tests/deployment/test_legacy_reviewer_off.py -q
bash -n scripts/runpod/start-final.sh
```

세 모델 영수증에서 두 모델만 선택하는지, 4B 요청이 생기지 않는지, 누락·잘못된 식별값과
예상 밖 GPU 모델을 거부하는지, 파서가 준비되지 않으면 시작을 차단하는지 검사한다.
Linux 감독 프로세스 검사는 임시 경로와 모의 서비스로 실제 시작 스크립트를 실행한다.
기존 대화 검사는 실패한 검토 결과를 저장한 뒤 재개하며, 이력 보존·재생성 방지·명시적 확인과
실행 결과를 확인한다.

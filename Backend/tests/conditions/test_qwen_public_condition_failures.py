"""Offline development regressions for public-9B baseline; not model scores.

R04 supplies the observed filter label with duplicate-only conditions. Other
cases protect grounding, checkpoint recovery and genuine mixed requests.
These tests were prepared while baseline-v2 ran; no application change or
test execution was performed as part of preparing this file.
"""
from copy import deepcopy

import pytest

from agent_builder.conditions.condition_readiness import literal_condition_issues, source_catalog
from tests.conditions.test_condition_readiness import checked, proposal, state


def table_state(text, columns=("customer_id", "amount")):
    current = state(text)
    current["resources"] = [{"id": "table", "kind": "table", "columns": list(columns)}]
    return current


def legacy_checkpoint(current, values, operations):
    """Construct a persisted model guess without treating today's validation as gold."""
    raw = proposal(current, values, operations)
    by_id = {entry["id"]: entry for entry in source_catalog(current)[0]}
    return {
        "operations": operations,
        "slots": {item["key"]: {**item, "evidence": [by_id[key] for key in item["evidence_ids"]]}
                  for item in raw["conditions"]},
        "decision": "ready", "validated_user_turn": 0,
    }


def answer_state(current, ledger, reply, key):
    restored = {**deepcopy(current), "condition_ledger": deepcopy(ledger),
                "pending_questions": deepcopy(ledger["questions"])}
    question = next(q for q in ledger["questions"] if q["key"] == key)
    restored["messages"] += [{"role": "assistant", "content": question["text"]},
                             {"role": "user", "content": reply}]
    restored["condition_answers"] = [{"question_ids": [question["id"]],
                                      "candidate_keys": [key], "answer": reply,
                                      "turn": len(restored["messages"]) - 1}]
    return restored


@pytest.mark.parametrize("guess", ["first", "last"])
def test_legacy_duplicate_retention_guess_is_revalidated_without_new_proposal(guess):
    current = table_state("customer_id 중복을 제거해서 JSON으로 저장해줘.")
    current["condition_ledger"] = legacy_checkpoint(current, {
        "duplicate.field": "customer_id", "duplicate.policy": guess, "output.format": "json",
    }, ["deduplicate"])
    result = checked(current, proposal(current, {}, ["deduplicate"]))
    assert result["decision"] == "clarify"
    assert result["slots"]["duplicate.policy"]["status"] == "missing"
    assert result["slots"]["duplicate.field"]["value"] == "customer_id"


@pytest.mark.parametrize("reply,expected", [("첫 행만 남김", "first"), ("마지막 행", "last")])
def test_actual_retention_answer_survives_another_checkpoint_validation(reply, expected):
    current = table_state("customer_id 중복을 제거해서 JSON으로 저장해줘.")
    first = checked(current, proposal(current, {
        "duplicate.field": "customer_id", "duplicate.policy": None, "output.format": "json",
    }, ["deduplicate"]))
    restored = answer_state(current, first, reply, "duplicate.policy")
    answered = checked(restored, proposal(restored, {"duplicate.policy": expected}, ["deduplicate"]))
    assert answered["decision"] == "ready"
    assert answered["slots"]["duplicate.policy"]["value"] == expected
    # The pending question is gone after saving; its actual answer event remains.
    restored.update(condition_ledger=deepcopy(answered), pending_questions=[])
    reopened = checked(restored, proposal(restored, {}, ["deduplicate"]))
    assert reopened["decision"] == "ready"
    assert reopened["slots"]["duplicate.policy"]["value"] == expected


def test_short_retention_words_do_not_answer_an_unrelated_question():
    current = table_state("customer_id 중복을 제거해줘.")
    first = checked(current, proposal(current, {"duplicate.field": "customer_id",
                                               "duplicate.policy": None}, ["deduplicate"]))
    unrelated = {"id": "a" * 24, "key": "output.format", "text": "출력 형식은 무엇인가요?",
                 "options": ["CSV", "JSON"]}
    first["questions"] = [unrelated]
    restored = answer_state(current, first, "마지막 행", "output.format")
    result = checked(restored, proposal(restored, {"duplicate.policy": "last"}, ["deduplicate"]))
    assert result["slots"]["duplicate.policy"]["status"] == "missing"


def test_empty_value_error_does_not_ground_duplicate_error():
    current = table_state("customer_id 중복을 제거해줘. 빈 값은 오류로 처리해. JSON으로 저장해.")
    result = checked(current, proposal(current, {"duplicate.field": "customer_id",
        "duplicate.policy": "error", "missing.policy": "error", "output.format": "json"}, ["deduplicate"]))
    assert result["slots"]["missing.policy"]["value"] == "error"
    assert result["slots"]["duplicate.policy"]["status"] == "missing"


def test_explicit_duplicate_and_empty_value_error_scopes_are_preserved():
    current = table_state("customer_id 중복이면 오류로 멈춰. 빈 값도 오류로 처리해. JSON으로 저장해.")
    result = checked(current, proposal(current, {"duplicate.field": "customer_id",
        "duplicate.policy": "error", "missing.policy": "error", "output.format": "json"}, ["deduplicate"]))
    assert result["decision"] == "ready"
    assert result["slots"]["duplicate.policy"]["value"] == "error"
    assert result["slots"]["missing.policy"]["value"] == "error"


def test_legacy_unwritten_time_is_revalidated_without_new_proposal():
    current = table_state("한국 시간으로 매일 표를 JSON으로 저장해줘.")
    current["condition_ledger"] = legacy_checkpoint(current, {
        "schedule.frequency": "daily", "schedule.hour": "9", "schedule.minute": "0",
        "schedule.timezone": "Asia/Seoul", "output.format": "json",
    }, ["schedule"])
    result = checked(current, proposal(current, {}, ["schedule"]))
    assert result["decision"] == "clarify"
    assert {"schedule.hour", "schedule.minute"} <= set(result["unresolved"])


@pytest.mark.parametrize("text,values,operations,key", [
    ("customer_id 중복을 제거해서 JSON으로 저장해줘.",
     {"duplicate.field": "customer_id", "duplicate.policy": "first", "output.format": "json"},
     ["deduplicate"], "duplicate.policy"),
    ("한국 시간으로 매일 표를 JSON으로 저장해줘.",
     {"schedule.frequency": "daily", "schedule.hour": "9", "schedule.minute": "0",
      "schedule.timezone": "Asia/Seoul", "output.format": "json"}, ["schedule"], "schedule.hour"),
])
def test_ready_checkpoint_audit_rejects_unwritten_values_even_before_new_extraction(text, values, operations, key):
    current = table_state(text)
    current.update(condition_ledger=legacy_checkpoint(current, values, operations), condition_enforced=True)
    assert any(key in issue for issue in literal_condition_issues(current))


@pytest.mark.parametrize("text,values", [
    ("한국 시간 매일 오전 9시에 표를 JSON으로 저장해줘.",
     {"schedule.frequency": "daily", "schedule.hour": "9", "schedule.minute": "0"}),
    ("한국 시간 매시간 정각에 표를 JSON으로 저장해줘.",
     {"schedule.frequency": "hourly", "schedule.minute": "0"}),
])
def test_explicit_clock_and_hourly_exact_hour_are_preserved(text, values):
    current = table_state(text)
    result = checked(current, proposal(current, {**values, "schedule.timezone": "Asia/Seoul",
                                               "output.format": "json"}, ["schedule"]))
    assert result["decision"] == "ready"


@pytest.mark.parametrize("reply,minute", [("정각", "0"), ("15분", "15")])
def test_hourly_minute_answer_is_scoped_to_actual_pending_question(reply, minute):
    current = table_state("한국 시간 매시간 표를 JSON으로 저장해줘.")
    first = checked(current, proposal(current, {"schedule.frequency": "hourly",
        "schedule.timezone": "Asia/Seoul", "output.format": "json"}, ["schedule"]))
    restored = answer_state(current, first, reply, "schedule.minute")
    result = checked(restored, proposal(restored, {"schedule.minute": minute}, ["schedule"]))
    assert result["decision"] == "ready"
    assert result["slots"]["schedule.minute"]["value"] == int(minute)


def test_ambiguous_hour_does_not_become_confirmed_from_a_model_value():
    current = table_state("한국 시간 매일 9시에 표를 JSON으로 저장해줘.")
    result = checked(current, proposal(current, {"schedule.frequency": "daily", "schedule.hour": "9",
        "schedule.minute": "0", "schedule.timezone": "Asia/Seoul", "output.format": "json"}, ["schedule"]))
    assert result["decision"] == "clarify"
    assert result["slots"]["schedule.hour"]["status"] != "specified"


@pytest.mark.parametrize("time_keys", [("schedule.minute", "schedule.hour"), ("schedule.hour", "schedule.minute")])
def test_grouped_time_question_preserves_slots_and_space_for_weekday(time_keys):
    current = table_state("한국 시간 매주 표를 JSON으로 저장해줘.")
    values = {"schedule.frequency": "weekly", "schedule.timezone": "Asia/Seoul", "output.format": "json"}
    values.update({key: None for key in time_keys})
    result = checked(current, proposal(current, values, ["schedule"]))
    assert {"schedule.hour", "schedule.minute", "schedule.weekday"} <= set(result["unresolved"])
    assert [q["key"] for q in result["questions"]] == ["schedule.hour", "schedule.weekday"]
    assert "분" in result["questions"][0]["text"]


@pytest.mark.parametrize("column,word,policy", [("customer_id", "첫", "first"), ("거래ID", "마지막", "last")])
def test_duplicate_only_conditions_do_not_create_a_filter_from_operation_label(column, word, policy):
    current = table_state(f"연결한 표에서 {column}가 중복이면 {word} 행만 남겨 JSON으로 저장해줘. 빈 값은 오류.",
                          columns=(column, "amount"))
    raw = proposal(current, {"duplicate.field": column, "duplicate.policy": policy,
                            "missing.policy": "error", "output.format": "json"}, ["filter"])
    result = checked(current, raw)
    assert result["decision"] == "ready"
    assert result["operations"] == ["deduplicate"]
    assert not any(key.startswith("filter.") for key in result["unresolved"])


@pytest.mark.parametrize("filter_values", [{}, {"filter.field": "amount", "filter.operator": None,
                                              "filter.value": None}])
def test_unresolved_real_filter_is_not_removed_by_duplicate_only_model_conditions(filter_values):
    current = table_state("amount가 큰 행만 골라 customer_id 중복이면 첫 행만 남겨 JSON으로 저장해줘.")
    result = checked(current, proposal(current, {**filter_values, "duplicate.field": "customer_id",
        "duplicate.policy": "first", "output.format": "json"}, ["filter"]))
    assert result["decision"] == "clarify"
    assert "filter" in result["operations"]
    assert {"filter.operator", "filter.value"} <= set(result["unresolved"])


def test_existing_grounded_filter_survives_a_later_duplicate_only_answer():
    current = table_state("amount가 20 이상인 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.")
    first = checked(current, proposal(current, {"filter.field": "amount", "filter.operator": "gte",
        "filter.value": "20", "missing.policy": "error", "duplicate.policy": "keep", "output.format": "json"}))
    current.update(condition_ledger=first, pending_questions=[])
    current["messages"].append({"role": "user", "content": "customer_id 중복이면 첫 행만 남겨줘."})
    result = checked(current, proposal(current, {"duplicate.field": "customer_id",
                                               "duplicate.policy": "first"}, ["deduplicate"]))
    assert result["decision"] == "ready"
    assert set(result["operations"]) == {"filter", "deduplicate"}
    assert result["slots"]["filter.value"]["value"] == 20


@pytest.mark.parametrize("policy", ["missing.policy", "duplicate.policy"])
def test_pure_schedule_does_not_promote_unrequested_model_policy_to_a_question(policy):
    current = table_state("한국 시간 매일 오전 9시에 표를 JSON으로 저장해줘.")
    result = checked(current, proposal(current, {"schedule.frequency": "daily", "schedule.hour": "9",
        "schedule.minute": "0", "schedule.timezone": "Asia/Seoul", "output.format": "json", policy: None}, ["schedule"]))
    assert result["decision"] == "ready"
    assert policy not in result["unresolved"]
    assert result["questions"] == []


@pytest.mark.parametrize("frequency,user_request,policy,answer_key,reply,initial_time,answer_values", [
    ("daily", "한국 시간으로 매일 표를 JSON으로 저장해줘.", "missing.policy", "schedule.hour",
     "매일 오전 9시 정각으로 해줘.", {"schedule.hour": None, "schedule.minute": "0"},
     {"schedule.hour": "9", "schedule.minute": "0"}),
    ("weekly", "한국 시간으로 매주 오전 9시에 표를 JSON으로 저장해줘.", "duplicate.policy", "schedule.weekday",
     "매주 월요일로 해줘.", {"schedule.hour": "9", "schedule.minute": "0", "schedule.weekday": None},
     {"schedule.hour": "},{", "schedule.minute": "},{", "schedule.weekday": "},{"}),
])
@pytest.mark.parametrize("repeat_spurious_policy", [False, True])
def test_m04_m05_legacy_unrequested_policy_does_not_block_actual_time_answer(
        frequency, user_request, policy, answer_key, reply, initial_time, answer_values, repeat_spurious_policy):
    current = table_state(user_request)
    values = {"schedule.frequency": frequency, "schedule.timezone": "Asia/Seoul",
              "output.format": "json", **initial_time, policy: None}
    # Model the already-persisted baseline state, not a ledger produced by the repaired code.
    previous = legacy_checkpoint(current, values, ["schedule"])
    previous.update(decision="clarify", unresolved=[answer_key, policy], questions=[
        {"id": "b" * 24, "key": answer_key, "text": "몇 시에 실행할까요?" if answer_key.endswith("hour")
         else "매주 무슨 요일에 실행할까요?", "options": []},
        {"id": "c" * 24, "key": policy, "text": "빈 값 또는 중복 정책을 알려주세요.", "options": []},
    ])
    restored = answer_state(current, previous, reply, answer_key)
    updates = {**answer_values, **({policy: None} if repeat_spurious_policy else {})}
    result = checked(restored, proposal(restored, updates, ["schedule"]))
    assert result["decision"] == "ready"
    assert result["slots"]["schedule.hour"]["value"] == 9
    assert result["slots"]["schedule.minute"]["value"] == 0
    if frequency == "weekly":
        assert result["slots"]["schedule.weekday"]["value"] == 0
    assert policy not in result["unresolved"]
    assert result["questions"] == []


@pytest.mark.parametrize("policy,phrase", [
    ("missing.policy", "빈 값 처리는 아직 미정이야."),
    ("duplicate.policy", "중복 처리는 아직 미정이야."),
])
def test_user_requested_unknown_policy_is_not_discarded_as_outside_schedule_scope(policy, phrase):
    current = table_state("한국 시간 매일 오전 9시에 표를 JSON으로 저장해줘. " + phrase)
    first = checked(current, proposal(current, {"schedule.frequency": "daily", "schedule.hour": "9",
        "schedule.minute": "0", "schedule.timezone": "Asia/Seoul", "output.format": "json", policy: None}, ["schedule"]))
    assert first["decision"] == "clarify"
    assert policy in first["unresolved"]
    assert any(q["key"] == policy for q in first["questions"])
    restored = answer_state(current, first, "모르겠어", policy)
    after_unknown = checked(restored, proposal(restored, {}, ["schedule"]))
    assert after_unknown["decision"] in {"clarify", "deferred"}
    assert policy in after_unknown["unresolved"]


def test_explicit_policies_survive_pure_schedule_scope_filtering():
    current = table_state("한국 시간 매일 오전 9시에 표를 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.")
    result = checked(current, proposal(current, {"schedule.frequency": "daily", "schedule.hour": "9",
        "schedule.minute": "0", "schedule.timezone": "Asia/Seoul", "output.format": "json"}, ["schedule"]))
    assert result["decision"] == "ready"
    assert result["slots"]["missing.policy"]["value"] == "error"
    assert result["slots"]["duplicate.policy"]["value"] == "keep"


def test_real_filter_missing_data_policy_remains_required_in_a_scheduled_workflow():
    current = table_state("한국 시간 매일 오전 9시에 amount가 20 이상인 행을 JSON으로 저장해줘.")
    current["resources"][0]["profile"] = {"amount": {"missing": 1}}
    result = checked(current, proposal(current, {"schedule.frequency": "daily", "schedule.hour": "9",
        "schedule.minute": "0", "schedule.timezone": "Asia/Seoul", "output.format": "json",
        "filter.field": "amount", "filter.operator": "gte", "filter.value": "20"}, ["filter", "schedule"]))
    assert result["decision"] == "clarify"
    assert "missing.policy" in result["unresolved"]
    assert any(q["key"] == "missing.policy" for q in result["questions"])


def test_real_deduplication_policy_remains_required_in_a_scheduled_workflow():
    current = table_state("한국 시간 매일 오전 9시에 customer_id 중복을 제거하고 JSON으로 저장해줘.")
    result = checked(current, proposal(current, {"schedule.frequency": "daily", "schedule.hour": "9",
        "schedule.minute": "0", "schedule.timezone": "Asia/Seoul", "output.format": "json",
        "duplicate.field": "customer_id", "duplicate.policy": None}, ["deduplicate", "schedule"]))
    assert result["decision"] == "clarify"
    assert "duplicate.policy" in result["unresolved"]
    assert any(q["key"] == "duplicate.policy" for q in result["questions"])


@pytest.mark.parametrize("clock,hour,minute", [("09:00", 9, 0), ("23:15", 23, 15)])
def test_explicit_24_hour_clock_is_not_treated_as_an_unwritten_default(clock, hour, minute):
    current = table_state(f"한국 시간 매일 {clock}에 표를 JSON으로 저장해줘.")
    result = checked(current, proposal(current, {"schedule.frequency": "daily",
        "schedule.hour": str(hour), "schedule.minute": str(minute), "schedule.timezone": "Asia/Seoul",
        "output.format": "json"}, ["schedule"]))
    assert result["decision"] == "ready"
    assert result["slots"]["schedule.hour"]["value"] == hour
    assert result["slots"]["schedule.minute"]["value"] == minute


def test_short_minute_reply_remains_grounded_after_pending_question_is_cleared():
    current = table_state("한국 시간 매시간 표를 JSON으로 저장해줘.")
    first = checked(current, proposal(current, {"schedule.frequency": "hourly",
        "schedule.timezone": "Asia/Seoul", "output.format": "json"}, ["schedule"]))
    restored = answer_state(current, first, "15분", "schedule.minute")
    answered = checked(restored, proposal(restored, {"schedule.minute": "15"}, ["schedule"]))
    restored.update(condition_ledger=answered, pending_questions=[], condition_enforced=True)
    assert literal_condition_issues(restored) == []
    assert checked(restored, proposal(restored, {}, ["schedule"]))["decision"] == "ready"


def test_negated_save_phrase_does_not_ground_first_row_retention():
    current = table_state("customer_id 중복이면 첫 행을 JSON으로 저장하지 마.")
    result = checked(current, proposal(current, {"duplicate.field": "customer_id",
        "duplicate.policy": "first", "output.format": "json"}, ["deduplicate"]))
    assert result["slots"]["duplicate.policy"]["status"] != "specified"


def test_negated_24_hour_clock_does_not_restore_a_model_guess():
    current = table_state("한국 시간 매일 09:00 말고 다른 시각으로 예약해줘.")
    result = checked(current, proposal(current, {"schedule.frequency": "daily",
        "schedule.hour": "9", "schedule.minute": "0", "schedule.timezone": "Asia/Seoul"}, ["schedule"]))
    assert result["slots"]["schedule.hour"]["status"] != "specified"


@pytest.mark.parametrize("extra", ["비싼 상품만", "amount가 20을 넘는 행만", "기준에 미달하는 행만"])
def test_unexplained_selection_clause_keeps_filter_even_when_model_omits_every_filter_slot(extra):
    current = table_state(f"customer_id 중복은 첫 행만 남기고 {extra} JSON으로 저장해줘. 빈 값은 오류.")
    result = checked(current, proposal(current, {"duplicate.field": "customer_id",
        "duplicate.policy": "first", "missing.policy": "error", "output.format": "json"}, ["filter"]))
    assert "filter" in result["operations"]
    assert result["decision"] == "clarify"
    assert {"filter.field", "filter.operator", "filter.value"} <= set(result["unresolved"])

"""A malformed provider response can use the extractor's existing repair attempt."""
import json
from copy import deepcopy
import pytest

from agent_builder.design.requirement_ledger import make_requirement_check
from tests.conditions.test_condition_readiness import checked, proposal, state


def test_missing_content_envelope_uses_second_normal_candidate():
    export = {"kind": "export", "format": "json"}
    candidate = {"description": "결과 저장", "source_message": 0, "spec": export}
    responses = iter([
        {"metrics": {"done_reason": "stop"}},
        {"content": json.dumps({"tasks": [candidate], "unresolved": []})},
    ])

    class Transport:
        def invoke(self, messages):
            return next(responses)

    result = make_requirement_check("fixture", Transport(), "enforce")(state("결과를 JSON으로 저장해줘."))
    assert result["requirement_route"] == "plan"
    assert result["trace"][-1]["model_calls"] == 2
    assert [t["spec"] for t in result["requirement_ledger"]["tasks"]] == [export]


@pytest.mark.parametrize('return_owned_candidate', [True, False])
def test_confirmed_numeric_filter_cannot_silently_remove_another_requested_predicate(return_owned_candidate):
    request = ("연결한 표에서 amount가 15 이상인 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해. "
               "customer_id가 VIP인 행이어야 해.")
    current = state(request)
    current["resources"] = [{"id": "sales", "name": "sales.json", "kind": "table",
                             "columns": ["customer_id", "amount"], "version": "1"}]
    values = {"filter.field": "amount", "filter.operator": "gte", "filter.value": "15",
              "output.format": "json", "missing.policy": "error", "duplicate.policy": "keep"}
    ledger = checked(current, proposal(current, values, ["filter"]))
    assert ledger["decision"] == "ready"
    current.update(condition_ledger=ledger, condition_enforced=True, condition_required=True)
    numeric = {"field": "amount", "operator": "gte", "value": 15, "value_type": "number", "compare_field": None}
    vip = {"field": "customer_id", "operator": "eq", "value": "VIP", "value_type": "text", "compare_field": None}
    candidate = {"description": "두 조건을 모두 적용", "source_message": 0,
                 "spec": {"kind": "filter", "match": "all", "filters": [numeric, vip]}}

    class Transport:
        def invoke(self, messages):
            return {"content": json.dumps({"tasks": [candidate] if return_owned_candidate else [], "unresolved": []})}

    active = Transport()
    result = make_requirement_check("fixture", active, "enforce")(current)
    assert result["requirement_route"] == "plan"
    filters = [f for t in result["requirement_ledger"]["tasks"]
               if t["spec"]["kind"] == "filter" for f in t["spec"]["filters"]]
    assert filters == [numeric, vip]
    branches = active.schema["$defs"]["RequiredTask"]["properties"]["spec"]["oneOf"]
    assert {"$ref":"#/$defs/Filter"} not in branches

    from agent_builder.design.condition_templates import compile_conditions
    from agent_builder.conditions.condition_readiness import plan_issues
    draft = compile_conditions(current)["draft"]
    assert plan_issues(ledger, draft, state=current) == []
    wrong = deepcopy(draft)
    run_filter = next(n for n in wrong["workflow"]["nodes"] if n["tool"] == "filter_sort")
    run_filter["settings"]["execution_plan"]["filters"].pop()
    assert plan_issues(ledger, wrong, state=current)

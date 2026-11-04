import json

import pytest
from openpyxl import load_workbook

from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from contracts.models import AgentDraft
from infrastructure.persistence.storage import Storage
from tests.design.test_builder import draft, initial
from tests.design.test_requirement_coverage import seed
from tests.workflow_engine.test_workflow import node
from workflow_engine.runner import run_workflow


def literal_writer(sheets, operation='create'):
    return node('out', 'create_spreadsheet', operation=operation,
                arguments_json=json.dumps({'sheets': sheets}))


@pytest.mark.parametrize('names,operation', [(['Data'], 'create'), (['A', 'B'], None)])
def test_user_supplied_literal_sheets_can_be_saved_and_downloaded(tmp_path, names, operation):
    sheets = [{'name': name, 'table': {'rows': [{'id': name.lower()}]}} for name in names]
    request = '다음 시트를 XLSX로 저장해줘. ' + json.dumps(sheets)
    state = {**initial(), 'messages': [{'role': 'user', 'content': request}], 'requirement_required': True}
    ledger = extract_plan({'tasks': [{'source_message': 0, 'description': '엑셀 저장',
                                      'spec': {'kind': 'export', 'format': 'xlsx'}}], 'unresolved': []}, state)
    workflow = {'entry': 'out', 'nodes': [literal_writer(sheets, operation)], 'edges': []}
    config = {**draft(), 'name': '지정된 값으로 시트 만들기', 'purpose': request, 'instructions': request,
              'tool_ids': ['create_spreadsheet'], 'workflow': workflow,
              'requirements': [{'description': '엑셀 저장', 'source_id': 'user', 'quote': request}]}
    state.update(draft=AgentDraft.model_validate(config).model_dump(), requirement_ledger=ledger,
                 requirement_fingerprint=ledger_fingerprint(state), requirement_digest=plan_digest(ledger))

    validated = validate_node(state)
    assert validated['status'] == 'ready', validated['issues']
    store = Storage(tmp_path)
    try:
        agent_id = store.save_agent({**state, **validated, 'confirmed': True})
        run = run_workflow(store, agent_id, request, 'unused')
        assert run['status'] == 'succeeded', run.get('error')
        book = load_workbook(tmp_path / run['report_path'], read_only=True)
        try:
            assert book.sheetnames == names
            assert [list(ws.values) for ws in book] == [[('id',), (name.lower(),)] for name in names]
        finally:
            book.close()
    finally:
        store.close()


def test_literal_workbook_cannot_replace_requested_processing_output():
    state = seed()
    state['requirement_ledger']['tasks'][3]['spec']['format'] = 'xlsx'
    state['requirement_digest'] = plan_digest(state['requirement_ledger'])
    state['draft']['workflow']['nodes'][-1] = literal_writer(
        [{'name': 'Data', 'table': {'rows': [{'amount': 30}]}}])
    state['draft']['tool_ids'][-1] = 'create_spreadsheet'
    assert coverage_report(state, state['draft'])['issues']

"""Current scorer behavior checks using synthetic labels, not model evidence."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/verify'))
spec = importlib.util.spec_from_file_location(
    'qwen_adjudication_under_test', ROOT / 'scripts/verify/adjudicate-qwen-live.py')
adjudicate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adjudicate)


@pytest.fixture
def clarification_case():
    corpus = json.loads((ROOT / 'docs/qwen-live-evaluation-cases.json').read_text(encoding='utf-8'))
    return deepcopy(next(case for case in corpus['cases'] if case['id'] == 'M07'))


@pytest.mark.parametrize(('formula', 'column', 'expected_completion', 'expected_answer'), [
    ('qty * 2', 'scaled', 1, 1),
    ('qty*2', 'scaled', 1, 1),
    ('qty\t*\n2', 'scaled', 1, 1),
    ('qty * 3', 'scaled', 0, 0),
    ('qty * 2', 'scaled ', 0, 1),
])
def test_clarification_completion_and_answer_scores_use_existing_slot_equivalence(
        clarification_case, formula, column, expected_completion, expected_answer):
    record = {
        'id': 'M07', 'final_route': 'READY',
        'turns': [{'response': {}}, {'response': {}}],
        'workflow': {'nodes': [
            {'id': 'read', 'tool': 'read_table', 'settings': {}},
            {'id': 'compute', 'tool': 'calculate_columns', 'settings': {
                'calculation_formula': formula, 'output_column': column}},
            {'id': 'output', 'tool': 'write_result', 'settings': {'format': 'json'}},
        ]},
    }
    label = {
        'intent_correct': True,
        'observed_slots': {'compute.output_column': 'scaled', 'output.format': 'json'},
        'final_observed_slots': {'compute.formula': formula, 'compute.output_column': column},
        'questions': [], 'task_coverage': [True, True, True],
        'evidence': 'Synthetic scorer fixture: checks existing comparison behavior only.',
    }
    result = adjudicate.score(record, clarification_case, label)['adjudicated_metrics']
    assert result['clarification_completion_rate'] == {
        'numerator': expected_completion, 'denominator': 1}
    assert result['answer_incorporation_accuracy'] == {
        'numerator': expected_answer, 'denominator': 1}


def test_formula_equivalence_does_not_normalize_business_column_names():
    assert adjudicate.same('compute.formula', 'qty * 2', 'qty*2')
    assert not adjudicate.same('compute.formula', 'qty * 2', 'qty * 3')
    assert not adjudicate.same('compute.output_column', 'scaled', 'scaled ')

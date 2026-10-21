"""Prevent transport adaptations from changing the supplied verifier contract."""
import ast
from pathlib import Path
import pytest
from a4b_verifier.client import SYSTEM

ROOT=Path(__file__).parents[1]
REFERENCE=ROOT/'integrations/qwen_verifier/a4b_verifier'

@pytest.mark.parametrize('name',['contracts.py','validation.py','service.py','adapters.py','registry.json','__init__.py','__main__.py'])
def test_verifier_contract_and_decisions_match_preserved_package(name):
    assert (ROOT/'a4b_verifier'/name).read_bytes()==(REFERENCE/name).read_bytes()


def test_connection_adaptation_does_not_rewrite_verifier_instructions():
    source=ast.parse((REFERENCE/'client.py').read_text())
    original=next(ast.literal_eval(n.value) for n in source.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SYSTEM' for t in n.targets))
    assert SYSTEM==original

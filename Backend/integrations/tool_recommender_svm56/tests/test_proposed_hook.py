import importlib.util
import builtins
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


class ProposedHookTests(unittest.TestCase):
    def setUp(self):
        self.agent = types.ModuleType('agent_builder'); self.agent.__path__ = []
        self.registry = types.ModuleType('agent_builder.registry'); self.registry.TOOLS = [{'id':'read_document'}]
        self.integrations = types.ModuleType('integrations'); self.integrations.__path__ = []
        self.subpackage = types.ModuleType('integrations.tool_recommender_svm56'); self.subpackage.__path__ = []
        self.modules = {'agent_builder':self.agent, 'agent_builder.registry':self.registry,
                        'integrations':self.integrations, 'integrations.tool_recommender_svm56':self.subpackage}

    def load(self):
        spec = importlib.util.spec_from_file_location('agent_builder.tool_recommendation', HERE.parents[2]/'agent_builder'/'tool_recommendation.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

    def test_off_mode_unchanged(self):
        with patch.dict(os.environ, {}, clear=True), patch.dict(sys.modules, self.modules):
            self.assertIsNone(self.load().recommend_for_state({}))

    def test_svm_opt_in_passes_current_registry(self):
        service = types.ModuleType('integrations.tool_recommender_svm56.svm56_service_hook')
        service.recommend_svm_for_state = lambda state, registry: {'state':state, 'registry':registry}
        with patch.dict(os.environ, {'A4B_TOOL_ADVICE_MODE':'hint', 'A4B_TOOL_ADVICE_BACKEND':'svm56'}, clear=True), \
             patch.dict(sys.modules, {**self.modules, service.__name__:service}):
            result = self.load().recommend_for_state({'messages':[]})
        self.assertEqual(result['registry'], self.registry.TOOLS)
        self.assertEqual(result['state'], {'messages':[]})

    def test_missing_opt_in_module_falls_back(self):
        original_import = builtins.__import__
        def missing_hook(name, *args, **kwargs):
            if name == 'integrations.tool_recommender_svm56.svm56_service_hook':
                raise ImportError('unavailable')
            return original_import(name, *args, **kwargs)
        with patch.dict(os.environ, {'A4B_TOOL_ADVICE_MODE':'hint', 'A4B_TOOL_ADVICE_BACKEND':'svm56'}, clear=True), \
             patch.dict(sys.modules, self.modules), patch('builtins.__import__', side_effect=missing_hook):
            result = self.load().recommend_for_state({})
        self.assertEqual(result['ml_status'], 'failed')
        self.assertEqual(result['ml_candidates'], [])

    def test_unknown_backend_falls_back(self):
        with patch.dict(os.environ, {'A4B_TOOL_ADVICE_MODE':'hint', 'A4B_TOOL_ADVICE_BACKEND':'bad'}, clear=True), \
             patch.dict(sys.modules, self.modules):
            result = self.load().recommend_for_state({})
        self.assertEqual(result['ml_status'], 'invalid_backend')

if __name__ == '__main__': unittest.main()

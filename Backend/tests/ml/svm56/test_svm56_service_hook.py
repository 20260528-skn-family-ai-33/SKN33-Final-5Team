import os
import unittest
from unittest.mock import patch

from ml.svm56 import svm56_service_hook as hook


class HookTests(unittest.TestCase):
    def tearDown(self):
        hook._load_pinned.cache_clear()

    def test_missing_configuration_returns_safe_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            result = hook.recommend_svm_for_state({}, [])
        self.assertEqual(result['ml_status'], 'missing_configuration')
        self.assertEqual(result['ml_candidates'], [])

    def test_model_load_failure_does_not_leak_exception(self):
        with patch.dict(os.environ, {'A4B_SVM_MODEL_PATH':'a', 'A4B_SVM_CATALOG_PATH':'b'}, clear=True), \
             patch.object(hook, '_load_pinned', side_effect=OSError('secret')):
            result = hook.recommend_svm_for_state({}, [])
        self.assertEqual(result['ml_status'], 'failed')
        self.assertEqual(result['ml_error_type'], 'OSError')
        self.assertNotIn('secret', str(result))

    def test_wrong_hash_fails_before_deserialization(self):
        with patch.object(hook, '_sha256', return_value='wrong-hash') as get_hash:
            with self.assertRaises(ValueError): hook._load_pinned('model', 'catalog')
        self.assertEqual(get_hash.call_count, 1)

    def test_advice_is_forwarded_to_existing_hook_contract(self):
        class Model:
            labels=['read_document']
            def predict(self, text, top_k):
                return {'selected_tool_ids':['read_document'], 'untrained_tool_ids':[],
                        'ranked_tools':[{'tool_id':'read_document','decision_score':0.7,'selected_by_threshold':True}]}
        with patch.dict(os.environ, {'A4B_SVM_MODEL_PATH':'a','A4B_SVM_CATALOG_PATH':'b'}, clear=True), \
             patch.object(hook, '_load_pinned', return_value=Model()):
            result = hook.recommend_svm_for_state({'messages':[{'role':'user','content':'읽기'}]}, [{'id':'read_document'}])
        self.assertEqual(result['ml_candidates'][0]['tool_id'], 'read_document')
        self.assertTrue(result['advisory_only'])
        self.assertNotIn('estimated_probability', result['ml_candidates'][0])

if __name__ == '__main__': unittest.main()

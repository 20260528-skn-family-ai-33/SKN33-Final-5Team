import unittest
from unittest.mock import patch
from pydantic import BaseModel
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI
from agent_builder.qwen_chat_model import QwenChatOpenAI, compact_structured_request


class TypedProbe(BaseModel):
    value: str
    count: int


class TypedTransportTests(unittest.TestCase):
    def test_real_parent_accepts_bound_schema_without_network(self):
        model = QwenChatOpenAI(model='Qwen/Qwen3-8B', api_key='unit-test-not-a-real-key', base_url='http://127.0.0.1:1/v1')
        runnable = model.with_structured_output(TypedProbe, method='json_schema', strict=True)
        self.assertGreater(len(runnable.steps), 1)

    def test_pydantic_format_is_a_strict_json_constraint(self):
        result = compact_structured_request({'response_format': TypedProbe,
            'extra_body': {'structured_outputs': {'whitespace_pattern': '[ ]?'}}})
        self.assertNotIn('response_format', result)
        schema = result['extra_body']['structured_outputs']['json']
        self.assertEqual(set(schema['required']), {'value', 'count'})
        self.assertIs(schema['additionalProperties'], False)

    def test_typed_result_preserves_runtime_model_dump_interface(self):
        model = QwenChatOpenAI(model='Qwen/Qwen3-8B', api_key='unit-test-not-a-real-key', base_url='http://127.0.0.1:1/v1')
        with patch.object(ChatOpenAI, 'with_structured_output', return_value=RunnableLambda(lambda _: {'value':'typed-ok','count':2})):
            result = model.with_structured_output(TypedProbe, strict=True).invoke('offline')
        self.assertIsInstance(result, TypedProbe)
        self.assertEqual(result.model_dump(), {'value':'typed-ok','count':2})

    def test_raw_result_preserves_validation_failure_as_parsing_error(self):
        model = QwenChatOpenAI(model='Qwen/Qwen3-8B', api_key='unit-test-not-a-real-key', base_url='http://127.0.0.1:1/v1')
        with patch.object(ChatOpenAI, 'with_structured_output', return_value=RunnableLambda(lambda _: {'raw':'placeholder','parsed':{'value':'x','count':'bad'},'parsing_error':None})):
            result = model.with_structured_output(TypedProbe, strict=True, include_raw=True).invoke('offline')
        self.assertIsNone(result['parsed'])
        self.assertIsNotNone(result['parsing_error'])


if __name__ == '__main__':
    unittest.main()

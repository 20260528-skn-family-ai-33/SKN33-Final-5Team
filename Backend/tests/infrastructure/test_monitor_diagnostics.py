from infrastructure.persistence.diagnostics import diagnostic_metadata


def test_validation_diagnostics_never_keep_input_context_or_provider_message(monkeypatch):
    monkeypatch.setenv('A4B_RELEASE', 'test-release')
    class ValidationError(Exception):
        status_code = 422
        def errors(self, **kwargs):
            assert kwargs == dict(include_input=False, include_context=False, include_url=False)
            return [dict(loc=('draft', 'nodes', 0, 'tool_id'), type='missing', input='private document', msg='secret body')]
    value = diagnostic_metadata(ValidationError('secret'), trace_id='request-id', model='qwen')
    assert value == dict(trace_id='request-id', model='qwen', release='test-release', http_status=422,
                         validation=[dict(field='draft.nodes.0.tool_id', type='missing')])
    assert 'secret' not in str(value) and 'private document' not in str(value)


def test_unknown_exception_is_safe_and_diagnostics_are_bounded():
    value = diagnostic_metadata(ValueError('private input'), trace_id='x'*1000)
    assert len(value['trace_id']) == 120
    assert value['validation'] == []
    assert 'private input' not in str(value)

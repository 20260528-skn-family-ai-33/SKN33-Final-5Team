"""Reject settings that the selected executor would silently ignore."""


def validate_execution_settings(node, *, operation):
    settings=node['settings']
    if operation:
        allowed={'operation','arguments_json','output_port','node_ref'}
        ignored=sorted(key for key,value in settings.items()
                       if key not in allowed and value is not None and value != [])
        if ignored:
            raise ValueError('확장 동작의 실행 계약에는 기본 설정을 섞을 수 없습니다: '+', '.join(ignored)+
                             '. operation의 arguments_json 또는 입력 연결을 사용하세요.')
    else:
        if settings.get('arguments_json') is not None:
            raise ValueError('기본 동작의 실행 계약은 arguments_json을 사용하지 않습니다. 기본 설정을 쓰거나 실제 operation을 지정하세요.')
        if settings.get('output_port') is not None and node['tool']!='saved_workflow':
            raise ValueError('기본 동작의 실행 계약은 출력 포트 재지정을 지원하지 않습니다. 등록된 출력 포트를 사용하세요.')
        if settings.get('document_table_id') is not None and (node['tool'] != 'read_document' or len(settings.get('file_ids') or []) != 1):
            raise ValueError('선택한 표는 원본 문서 하나의 읽기 노드에 연결해야 합니다.')

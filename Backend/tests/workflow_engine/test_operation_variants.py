"""Exercise the additional operations, beyond one representative per ML label.

Reuses local synthetic fixtures; model and external provider calls are injected.
This checks executable contracts, not Qwen quality or live account integration.
"""
import base64
import json
from types import SimpleNamespace

import pytest

from tests.workflow_engine.test_business56 import business_files, examples, MODEL_RESULTS
from tests.workflow_engine.test_workflow import node, saved
from workflow_engine.catalog.canonical import OPERATIONS
from workflow_engine.catalog.registry import node_ports
from workflow_engine.runner import run_workflow


EXTRA_KEYS=[
 ('read_document','read_text'),('read_table','read_csv'),('search_document','keyword_search'),
 ('compose_answer','summarize'),('filter_sort','handle_missing'),('filter_sort','filter'),
 ('filter_sort','sort'),('filter_sort','select_columns'),('aggregate_table','count'),
 ('aggregate_table','group_aggregate'),('calculate_columns','rename_columns'),
 ('calculate_columns','parse_dates'),('calculate_columns','calculate_records'),
 ('calculate_columns','cast_numbers'),('join_tables','join_records'),
 ('edit_spreadsheet','add_sheet'),('edit_spreadsheet','set_cells'),('edit_spreadsheet','fill_sequence'),
 ('edit_document','insert_text'),('edit_document','delete_text'),
 ('export_result','write_json'),('export_result','write_csv'),
 ('verify_content_requirements','check_expected'),('check_document_links','check_remote_targets'),
 ('text_statistics','measure_text'),('split_pdf','split'),('text_transform','split'),
 ('text_transform','combine'),('json_transform','parse'),('list_transform','select'),
 ('date_transform','format'),('url_transform','parse'),('email.list','read_messages')]


def extra_arguments(root):
    base=examples(root)
    table=base['join_tables'][1]['left']
    source=base['compose_answer'][1]['evidence'][0]
    d=base['edit_document'][1]['document']
    workbook=base['edit_spreadsheet'][1]['workbook']
    version=base['edit_spreadsheet'][1]['expected_version']
    return {
      ('read_document','read_text'):{'path':'a.txt'},
      ('read_table','read_csv'):{'path':'a.csv'},
      ('search_document','keyword_search'):{'documents':[source],'keywords':['Revenue'],'top_k':1},
      ('compose_answer','summarize'):{'documents':[source],'scope':'revenue','max_characters':100},
      ('filter_sort','handle_missing'):{'table':{'rows':[{'id':1,'amount':None}]},'columns':['amount'],'action':'fill','fill_value':0},
      ('filter_sort','filter'):{'rows':table['rows'],'predicates':[{'column':'amount','op':'ge','value':20}]},
      ('filter_sort','sort'):{'rows':table['rows'],'keys':[{'column':'amount','descending':True}]},
      ('filter_sort','select_columns'):{'rows':table['rows'],'columns':['amount']},
      ('aggregate_table','count'):{'table':table,'group_by':[],'mode':'rows'},
      ('aggregate_table','group_aggregate'):{'rows':table['rows'],'group_by':[],'operation':'sum','column':'amount'},
      ('calculate_columns','rename_columns'):{'table':table,'mapping':{'amount':'total'}},
      ('calculate_columns','parse_dates'):{'table':{'rows':[{'date':'2026-11-03'}]},'columns':['date'],'input_format':'%Y-%m-%d','timezone':'Asia/Seoul'},
      ('calculate_columns','calculate_records'):{'rows':table['rows'],'output_column':'double','formula':'amount*2'},
      ('calculate_columns','cast_numbers'):{'rows':[{'amount':'10'}],'columns':['amount']},
      ('join_tables','join_records'):{'left':table['rows'],'right':[{'id':1,'name':'A'}],'keys':['id'],'how':'left'},
      ('edit_spreadsheet','add_sheet'):{'workbook':workbook,'expected_version':version,'sheet':{'name':'Second','table':table}},
      ('edit_spreadsheet','set_cells'):{'workbook':workbook,'expected_version':version,'cells':[{'sheet_name':'Data','cell':'A2','value':7}]},
      ('edit_spreadsheet','fill_sequence'):{'workbook':workbook,'expected_version':version,'sheet_name':'Data','cell_range':'A2:A3','start':2,'step':1},
      ('edit_document','insert_text'):{'document':d,'expected_version':d['version'],'anchor':'Revenue','position':'after','text':' value'},
      ('edit_document','delete_text'):{'document':d,'expected_version':d['version'],'target':'Revenue ','occurrence':'all'},
      ('export_result','write_json'):{'path':'extra.json','value':table['rows']},
      ('export_result','write_csv'):{'path':'extra.csv','rows':table['rows'],'columns':table['columns']},
      ('verify_content_requirements','check_expected'):{'extracted':[{'field':'amount','value':30}],'expected':[{'field':'amount','value':30}]},
      ('check_document_links','check_remote_targets'):{'document':{'text':'[fixture](https://example.invalid/)'},'timeout_seconds':1},
      ('text_statistics','measure_text'):{'text':'A B','unit':'characters','include_whitespace':False},
      ('split_pdf','split'):{'asset':'a.pdf','groups':[{'page_numbers':[1]}]},
      ('text_transform','split'):{'text':'A,B','separator':','},
      ('text_transform','combine'):{'items':['A','B'],'separator':','},
      ('json_transform','parse'):{'text':'{"amount":30}'},
      ('list_transform','select'):{'items':[{'amount':10},{'amount':20}],'index':1},
      ('date_transform','format'):{'timestamp':'2026-11-03T10:00:00+09:00','format':'%Y-%m-%d'},
      ('url_transform','parse'):{'url':'https://example.invalid/a?x=1'},
      ('email.list','read_messages'):{'query':'is:unread','max_results':2},
    }


def test_representative_and_extra_cases_cover_all_operation_ids(business_files):
    baseline={(tool,operation) for tool,(operation,_) in examples(business_files).items()}
    extras=set(extra_arguments(business_files))
    registered={(tool,spec['id']) for tool,specs in OPERATIONS.items() for spec in specs}
    assert not baseline & extras
    assert extras==set(EXTRA_KEYS)
    assert baseline | extras==registered


@pytest.mark.parametrize('tool,operation',EXTRA_KEYS)
def test_additional_operation_runs_with_declared_output_ports(tmp_path,business_files,tool,operation):
    args=extra_arguments(business_files)[tool,operation]
    step=node('operation','%s'%tool,operation=operation,arguments_json=json.dumps(args))
    store,identifier,_=saved(tmp_path,{'entry':'operation','nodes':[step],'edges':[]})
    calls=[]
    def head(op,arguments):
        calls.append((op,arguments));return {'reachable':True,'status_code':200}
    def gmail(op,arguments):
        calls.append((op,arguments))
        if op=='email.list':
            if arguments.get('page_token')=='older':return {'messages':[{'id':'mail-old'}]}
            assert 'page_token' not in arguments
            return {'messages':[{'id':'mail-new'}],'nextPageToken':'older'}
        assert op=='email.get' and arguments['format']=='full'
        identifier=arguments['message_id']
        assert identifier in ('mail-new','mail-old')
        body=base64.urlsafe_b64encode(('실제 본문 '+identifier).encode()).decode().rstrip('=')
        return {'id':identifier,'payload':{'mimeType':'text/plain',
                'headers':[{'name':'Subject','value':'제목 '+identifier}], 'body':{'data':body}}}
    store.workflow_connectors={'http_head':head,'gmail':gmail}
    class Model:
        def invoke(self,messages):
            calls.append(messages)
            return SimpleNamespace(content=json.dumps(MODEL_RESULTS['compose_answer']))
    try:
        result=run_workflow(store,identifier,'기존 로컬 합성 자료로 실행','unused',llm=Model())
        assert result['status']=='succeeded',result.get('error')
        outputs=result['outputs']['operation']
        assert set(node_ports(step)[1])<=set(outputs)
        if tool=='filter_sort':
            assert result['steps'][0]['parameters']['operation']==operation
            assert json.loads(result['steps'][0]['parameters']['arguments_json'])==args
        if 'artifact' in outputs:
            data=base64.b64decode(result['artifact']['content'])
            assert (tmp_path/result['report_path']).read_bytes()==data
        if tool in ('compose_answer','check_document_links'):assert len(calls)==1
        if (tool,operation)==('filter_sort','filter'):assert outputs['records']==[{'id':2,'amount':20}]
        if (tool,operation)==('aggregate_table','group_aggregate'):assert outputs['records']==[{'sum':30}]
        if (tool,operation)==('calculate_columns','cast_numbers'):assert outputs['records']==[{'amount':10}]
        if (tool,operation)==('json_transform','parse'):assert outputs['result']=={'amount':30}
        if (tool,operation)==('email.list','read_messages'):
            assert calls==[
                ('email.list',{'query':'is:unread','max_results':2}),
                ('email.list',{'query':'is:unread','max_results':1,'page_token':'older'}),
                ('email.get',{'message_id':'mail-new','format':'full'}),
                ('email.get',{'message_id':'mail-old','format':'full'}),
            ]
            documents=outputs['documents']
            assert [doc['source_id'] for doc in documents]==['gmail:mail-new','gmail:mail-old']
            assert all('실제 본문 '+identifier in doc['text']
                       for identifier,doc in zip(('mail-new','mail-old'),documents))
            assert outputs['result']['returned_count']==outputs['result']['requested_count']==2
            assert outputs['result']['complete'] and outputs['result']['search_complete']
            assert outputs['result']['pages_read']==2
    finally:store.close()

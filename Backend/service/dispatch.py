"""Route one Node API job (`action`) to the feature that owns it.

Actions not listed here are design-session actions: they load the session and its
LangGraph conversation first, exactly as the original single worker did.
"""
from infrastructure.persistence.storage import Storage
from workflow_engine.lifecycle.run_lifecycle import RunLifecycle
from workflow_engine.public_runs import public_result
from service.handlers import asset_library, design, generation, library, library_sources, reviews, runs, schedules, uploads


class Context:
    def __init__(self,data,storage,lifecycle,conversation):
        self.data=data;self.storage=storage;self.lifecycle=lifecycle;self.conversation=conversation
        self.action=data['action'];self.api_key=data.get('api_key','')


ACTIONS={
    'schedule_source':library_sources.source,
    'library_source_plan':library_sources.plan,
    'library_source_original_plan':library_sources.original_plan,
    'library_source_export':library_sources.export,
    'library_source_resolve':library_sources.resolve,
    'begin_generation':generation.begin,
    'interrupt_generation':generation.interrupt,
    'generation_history':generation.listing,
    'recover_generation':generation.recover,
    'recover_runs':runs.recover_runs,
    'interrupt_run':runs.interrupt_run,
    'heartbeat_run':runs.heartbeat_run,
    'nodes':library.nodes,
    'save_node':library.save_node,
    'catalog':library.catalog,
    'review_decision':reviews.review_decision,
    'approved_replies':reviews.approved_replies,
    'retry_run':runs.retry_run,
    'asset':reviews.asset,
    'assets':uploads.assets,
    'library':asset_library.listing,
    'validate_destination':asset_library.validate_destination,
    'library_upload':asset_library.upload,
    'folder_create':asset_library.create_folder,
    'folder_update':asset_library.update_folder,
    'folder_delete':asset_library.delete_folder,
    'asset_move':asset_library.move_asset,
    'asset_preview':uploads.preview,
    'original_registered':uploads.original_registered,
    'prepare_asset_delete':uploads.prepare_delete,
    'finish_asset_delete':uploads.finish_delete,
    'document_review':reviews.document_review,
    'agents':library.agents,
    'schedules':library.schedules,
    'agent':library.agent,
    'rename_agent':library.rename_agent,
    'workflow_folders':library.workflow_folders,
    'claim_due':schedules.claim_due,
    'schedule':schedules.schedule,
    'schedule_configure':schedules.schedule_configure,
    'schedule_remove':schedules.schedule_remove,
    'schedule_test':schedules.schedule_test,
    'schedule_cancel':schedules.schedule_cancel,
    'fail_job':schedules.fail_job,
    'scheduled_run':schedules.scheduled_run,
    'source_upload':schedules.source_upload,
    'new_session':design.new_session,
    'run_upload':runs.run_upload,
    'resume_run':runs.resume_run,
    'run':runs.run,
    'test':runs.test,
}


def handle(data,conversation=None):
    storage=Storage(data['directory'])
    try:
        connectors={}
        token=data.get('gmail_access_token')
        if token and data['action'] in {'test','resume_run','retry_run','scheduled_run'}:
            from workflow_engine.unified_tools.expansion.google_adapters import GmailAdapter, GoogleApiError, GoogleJsonTransport

            transport=GoogleJsonTransport(lambda: token,'https://gmail.googleapis.com')
            gmail=GmailAdapter(transport,'',data['directory'])

            def read_gmail(operation,arguments):
                if operation not in {'email.list','email.get','email.attachment.get'}:
                    raise ValueError('이 Gmail 연결에는 읽기 권한이 있습니다. 발송 대신 답장 초안을 작성해주세요.')
                try:
                    return gmail(operation,arguments)
                except GoogleApiError as error:
                    if error.status in {401,403}:
                        raise ValueError('Gmail에서 읽기 요청을 허용하지 않았습니다. 계정 연결과 읽기 권한을 다시 확인해주세요.') from None
                    raise

            connectors['gmail']=read_gmail
        slack_token=data.get('slack_access_token')
        if slack_token and data['action'] in {'test','resume_run','retry_run','scheduled_run'}:
            from workflow_engine.unified_tools.expansion.slack_adapters import SlackAdapter, SlackJsonTransport

            connectors['slack']=SlackAdapter(SlackJsonTransport(lambda: slack_token))
        if connectors:storage.workflow_connectors=connectors
        view=storage
        if data.get('library_source_snapshot') is not None:
            from workflow_engine.scheduling.library_sources import LibrarySources
            view=LibrarySources(storage).execution_storage(data)
        context=Context(data,view,RunLifecycle(view),conversation)
        return public_result(ACTIONS.get(context.action,design.session_action)(context))
    finally:
        storage.close()

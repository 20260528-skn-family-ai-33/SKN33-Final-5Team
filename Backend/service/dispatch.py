"""Route one Node API job (`action`) to the feature that owns it.

Actions not listed here are design-session actions: they load the session and its
LangGraph conversation first, exactly as the original single worker did.
"""
from infrastructure.persistence.storage import Storage
from workflow_engine.lifecycle.run_lifecycle import RunLifecycle
from service.handlers import design, library, reviews, runs, schedules


class Context:
    def __init__(self,data,storage,lifecycle,conversation):
        self.data=data;self.storage=storage;self.lifecycle=lifecycle;self.conversation=conversation
        self.action=data['action'];self.api_key=data.get('api_key','')


ACTIONS={
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
    'document_review':reviews.document_review,
    'agents':library.agents,
    'agent':library.agent,
    'claim_due':schedules.claim_due,
    'schedule':schedules.schedule,
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
        context=Context(data,storage,RunLifecycle(storage),conversation)
        return ACTIONS.get(context.action,design.session_action)(context)
    finally:
        storage.close()

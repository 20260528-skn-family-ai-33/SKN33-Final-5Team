"""Transform diagnostics into an explicitly unapproved report table."""
from ..review_state import candidate_reply, collect_reviews

def execute(node,state,storage,config,llm):
    binding=next(b for b in node['bindings'] if b['input']=='reviews')
    bound=state['outputs'][binding['node_id']][binding['output']]
    latest={r['id']:r for r in collect_reviews(state['outputs'])}
    reviews=collect_reviews({'bound':{'reviews':[latest.get(r['id'],r) for r in bound]}})
    columns=['review_id','verdict','summary','candidate_reply','approval_state']
    rows=[{'review_id':r['id'],'verdict':r['report']['verdict'],'summary':r['report']['summary'],'candidate_reply':candidate_reply(r),'approval_state':'pending'} for r in reviews]
    return {'table':{'rows':rows,'columns':columns,'file_id':None,'review_report':True}}

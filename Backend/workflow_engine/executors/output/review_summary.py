from verification.reply_review.state import candidate_reply, collect_reviews

COLUMNS=['review_id','verdict','summary','candidate_reply','approval_state']


def execute(node,state,storage,config,llm):
    tool=node["tool"];settings=node["settings"];inputs={b["input"]:state["outputs"][b["node_id"]][b["output"]] for b in node["bindings"]}
    bound=collect_reviews({'bound':{'reviews':inputs['reviews']}})
    # A later rewrite node may have replaced the candidate; report the latest one.
    current={review['id']:review for review in collect_reviews(state.get('outputs',{}))}
    rows=[]
    for review in bound:
        review=current.get(review['id'],review)
        rows.append({'review_id':review['id'],'verdict':review['report']['verdict'],'summary':review['report']['summary'],
                     'candidate_reply':candidate_reply(review),'approval_state':'pending'})
    return {'table':{'rows':rows,'columns':list(COLUMNS),'file_id':None,'review_report':True}}

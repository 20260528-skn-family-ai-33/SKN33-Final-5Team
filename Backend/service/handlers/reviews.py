"""Human review jobs: A4 reply decisions and document parse review."""


def review_decision(c):
    from verification.reply_review.decisions import decide
    data=c.data
    return decide(c.storage,data['id'],data['review_id'],data['decision'],data.get('edited_reply',''))

def approved_replies(c):
    from verification.reply_review.decisions import approved_csv
    return approved_csv(c.storage,c.data['id'])

def asset(c):return {'asset':c.storage.asset(c.data['id'])}

def document_review(c):
    from documents.review.document_review import view, save
    data=c.data
    return save(c.storage,data['id'],data['command']) if 'command' in data else view(c.storage,data['id'])

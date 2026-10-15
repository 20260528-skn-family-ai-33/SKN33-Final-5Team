"""Final human decisions share A4B's SQLite database and immutable run snapshots."""
import csv,io,json
from .storage import now
from .review_state import collect_reviews

def decorate_run(storage,run):
    reviews=collect_reviews(run.get('outputs',{}))
    decisions={row[0]:json.loads(row[1]) for row in storage.db.execute('SELECT review_id,json FROM reply_review_decisions WHERE run_id=?',(run['id'],))}
    run['reviews']=[{**r,'decision':decisions.get(r['id'])} for r in reviews]
    run['approval_status']=('pending' if any(r['id'] not in decisions for r in reviews) else 'decided') if reviews else None
    return run

def decide(storage,run_id,review_id,decision,edited_reply):
    if decision not in ['approve','reject']:raise ValueError('승인 또는 거절을 선택하세요.')
    if not isinstance(edited_reply,str) or len(edited_reply)>12000:raise ValueError('응대문은 12,000자 이하로 입력하세요.')
    edited_reply=edited_reply.strip()
    if decision=='approve' and not edited_reply:raise ValueError('승인할 응대문을 입력하세요.')
    if decision=='reject':edited_reply=''
    storage.db.execute('BEGIN IMMEDIATE')
    try:
        run=storage.test(run_id)
        if run['status']!='succeeded':raise ValueError('검수에 성공한 실행만 승인·거절할 수 있습니다.')
        review=next((r for r in run['reviews'] if r['id']==review_id),None)
        if not review:raise ValueError('해당 실행의 검수 보고서를 찾지 못했습니다.')
        previous=review.get('decision')
        if previous:
            if previous['decision']!=decision or previous['edited_reply']!=edited_reply:raise ValueError('이미 결정된 응대입니다. 새 실행으로 다시 검수하세요.')
        else:
            result={'decision':decision,'edited_reply':edited_reply,'decided_at':now()}
            storage.db.execute('INSERT INTO reply_review_decisions VALUES(?,?,?)',(run_id,review_id,json.dumps(result,ensure_ascii=False)))
        storage.db.commit()
    except Exception:
        storage.db.rollback();raise
    return storage.test(run_id)

def approved_csv(storage,agent_id):
    storage.agent(agent_id)
    stream=io.StringIO(newline='');writer=csv.writer(stream)
    writer.writerow(['run_id','agent_version','source_version','review_id','customer_message','verdict','approved_reply','approved_at'])
    def cell(value):
        text=str(value if value is not None else '')
        return "'"+text if text.lstrip().startswith(('=','+','-','@','\t','\r')) else text
    rows=storage.db.execute('SELECT t.json,d.review_id,d.json FROM reply_review_decisions d JOIN agent_tests t ON t.id=d.run_id WHERE t.agent_id=? ORDER BY d.rowid',(agent_id,)).fetchall()
    for raw,id,decision_json in rows:
        decision=json.loads(decision_json)
        if decision['decision']!='approve':continue
        run=json.loads(raw);review=next((r for r in collect_reviews(run.get('outputs',{})) if r['id']==id),None)
        if not review:raise ValueError('승인한 응대의 검수 기록이 없습니다.')
        writer.writerow([cell(v) for v in [run['id'],run['agent_version'],run.get('source_version'),id,review['customer_message'],review['report']['verdict'],decision['edited_reply'],decision['decided_at']]])
    return {'name':'승인된_응대.csv','content':'\ufeff'+stream.getvalue(),'mime':'text/csv; charset=utf-8'}

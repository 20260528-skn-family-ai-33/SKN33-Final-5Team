"""Gmail/Google Calendar adapters. Standard library only; no automatic live calls.
Transport/token injection allows offline tests without exposing connector credentials.
"""
from __future__ import annotations
import base64,datetime,json,mimetypes,os,re,socket
from email.message import EmailMessage, Message
from email import policy
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.parse import quote,urlencode
from urllib.request import Request,build_opener,HTTPRedirectHandler
class MissingCredentials(RuntimeError):pass
class GoogleApiError(RuntimeError):
    def __init__(self,status):
        self.status=status
        super().__init__(f"Google API HTTP {status}; inspect credentials/scopes, quota or resource version.")
class DeliveryStateUnknown(RuntimeError):pass
class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
class EnvironmentAccessToken:
    """Read a token at request time; no token is persisted in config or test reports."""
    def __init__(self,variable="A4B_GOOGLE_ACCESS_TOKEN"):self.variable=variable
    def __call__(self):
        value=os.environ.get(self.variable,"").strip()
        if not value:raise MissingCredentials("Access token is not configured: "+self.variable)
        if "\r" in value or "\n" in value:raise ValueError("Invalid access token")
        return value
class GoogleJsonTransport:
    def __init__(self,token_provider,origin,timeout=20,max_bytes=15_000_000,opener=None):
        if origin not in {"https://gmail.googleapis.com","https://www.googleapis.com"}:raise ValueError("Unapproved Google origin")
        self.token_provider=token_provider;self.origin=origin;self.timeout=timeout;self.max_bytes=max_bytes
        self.opener=opener or build_opener(NoRedirects())
    def request(self,method,path,query=None,payload=None,etag=None):
        if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path:raise ValueError("Invalid API path")
        token=self.token_provider()
        headers={"Authorization":"Bearer "+token,"Accept":"application/json"}
        if etag:
            clean_text(etag,"etag");headers["If-Match"]=etag
        url=self.origin+path
        if query:url+="?"+urlencode({k:v for k,v in query.items() if v is not None},doseq=True)
        data=None
        if payload is not None:data=json.dumps(payload,ensure_ascii=False).encode();headers["Content-Type"]="application/json; charset=utf-8"
        req=Request(url,data=data,headers=headers,method=method)
        try:
            with self.opener.open(req,timeout=self.timeout) as response:
                body=response.read(self.max_bytes+1)
                if len(body)>self.max_bytes:raise ValueError("API response too large")
                return json.loads(body) if body else {}
        except HTTPError as e:raise GoogleApiError(e.code) from None
        except (URLError,TimeoutError,socket.timeout,OSError):
            if method not in {"GET","HEAD"}:raise DeliveryStateUnknown("Write outcome unknown. Verify provider state before retrying; no automatic retry occurred.") from None
            raise ConnectionError("Google API connection failed") from None
def clean_text(value,name):
    if not isinstance(value,str) or "\r" in value or "\n" in value:raise ValueError("Invalid "+name)
    return value
def identifier(value,name):
    clean_text(value,name)
    if not value or len(value)>2048:raise ValueError("Invalid "+name)
    return quote(value,safe="")
def email_address(value):
    clean_text(value,"email address")
    if not re.fullmatch(r"[^@\s<>;,]+@[^@\s<>;,]+\.[^@\s<>;,]+",value):raise ValueError("Use an explicit email address, not a display name")
    return value
def recipients(values,required=False):
    if values is None:values=[]
    if isinstance(values,str):values=[values]
    if not isinstance(values,list) or any(not isinstance(v,str) for v in values):raise ValueError("Recipients must be a list of addresses")
    if required and not values:raise ValueError("At least one To recipient is required")
    return [email_address(v) for v in values]
def prepare_message(sender,to,subject,body,cc=None,bcc=None,attachments=None,root=None,max_message_bytes=12_000_000):
    email_address(sender);clean_text(subject,"subject")
    if not subject.strip():raise ValueError("Subject required")
    if not isinstance(body,str):raise ValueError("Body must be text")
    tos=recipients(to,True);ccs=recipients(cc);bccs=recipients(bcc)
    message=EmailMessage(policy=policy.SMTP)
    message["From"]=sender;message["To"]=", ".join(tos);message["Subject"]=subject
    if ccs:message["Cc"]=", ".join(ccs)
    if bccs:message["Bcc"]=", ".join(bccs)
    message.set_content(body,charset="utf-8")
    attached=[]
    for spec in attachments or []:
        if root is None:raise ValueError("Authorized attachment root required")
        relative=spec["path"];base=Path(root).resolve();p=(base/relative).resolve()
        if not p.is_relative_to(base):raise ValueError("Attachment leaves authorized root")
        if not p.is_file() or p.stat().st_size>max_message_bytes:raise ValueError("Invalid or oversized attachment")
        content_type=spec.get("content_type") or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if not re.fullmatch(r"[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+",content_type):raise ValueError("Invalid attachment content type")
        maintype,subtype=content_type.split("/")
        message.add_attachment(p.read_bytes(),maintype=maintype,subtype=subtype,filename=p.name);attached.append(p.name)
    raw=message.as_bytes()
    if len(raw)>max_message_bytes:raise ValueError("Encoded message exceeds configured limit")
    return {"raw":base64.urlsafe_b64encode(raw).decode(),"to":tos,"cc":ccs,"bcc":bccs,"subject":subject,"attachments":attached,
            "size_bytes":len(raw),"sent":False}
class GmailAdapter:
    def __init__(self,transport,sender,root):self.transport=transport;self.sender=sender;self.root=Path(root)
    def __call__(self,operation,arguments):
        op=operation.removeprefix("email.")
        handlers={"prepare":self.prepare,"send":self.send,"list":self.list,"get":self.get,"attachment.get":self.attachment_get}
        if op not in handlers:raise ValueError("Unknown Gmail operation")
        return handlers[op](**arguments)
    def prepare(self,to,subject,body,cc=None,bcc=None,attachments=None):
        return prepare_message(self.sender,to,subject,body,cc,bcc,attachments,self.root)
    def send(self,to,subject,body,cc=None,bcc=None,attachments=None):
        message=self.prepare(to,subject,body,cc,bcc,attachments)
        response=self.transport.request("POST","/gmail/v1/users/me/messages/send",payload={"raw":message["raw"]})
        if not response.get("id"):raise DeliveryStateUnknown("Send response has no message ID; inspect provider before retry.")
        return {"provider":"gmail","message_id":response["id"],"thread_id":response.get("threadId"),"status":"accepted_by_provider",
                "recipient_delivery_confirmed":False}
    def list(self,query=None,max_results=20,page_token=None):
        if isinstance(max_results,bool) or not isinstance(max_results,int) or not 1<=max_results<=500:raise ValueError("max_results must be 1..500")
        return self.transport.request("GET","/gmail/v1/users/me/messages",query={"q":query,"maxResults":max_results,"pageToken":page_token})
    def get(self,message_id,format="full"):
        if format not in {"full","minimal","metadata","raw"}:raise ValueError("Unknown Gmail format")
        return self.transport.request("GET","/gmail/v1/users/me/messages/"+identifier(message_id,"message_id"),query={"format":format})
    def attachment_get(self,message_id,attachment_id):
        return self.transport.request("GET","/gmail/v1/users/me/messages/"+identifier(message_id,"message_id")+"/attachments/"+identifier(attachment_id,"attachment_id"))


class GmailHtmlText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts=[]
        self.hidden=0

    def handle_starttag(self,tag,attrs):
        if tag in {'script','style','head'}: self.hidden+=1
        elif tag in {'br','p','div','li','tr'}: self.parts.append('\n')

    def handle_endtag(self,tag):
        if tag in {'script','style','head'}: self.hidden=max(0,self.hidden-1)

    def handle_data(self,data):
        if not self.hidden: self.parts.append(data)


def gmail_body_reader(call):
    """Resolve external text bodies within one account-scoped, bounded read batch.

    MessagePartBody.data can be empty when its content is served separately:
    https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages.attachments
    These limits bound additional body reads; file attachments never reach this resolver.
    """
    requests=0;size=0;cache={}
    max_requests=20;max_bytes=10_000_000

    def read(message_id,body):
        nonlocal requests,size
        attachment_id=body.get('attachmentId')
        if not isinstance(attachment_id,str) or not attachment_id:
            raise ValueError('분리된 Gmail 본문의 ID를 확인하지 못했습니다.')
        declared=body.get('size')
        if declared is not None and (type(declared) is not int or declared<0):
            raise ValueError('분리된 Gmail 본문의 크기를 확인하지 못했습니다.')
        key=(message_id,attachment_id)
        if key in cache:
            decoded=cache[key]
        else:
            if requests>=max_requests or declared is not None and declared>max_bytes-size:
                raise ValueError('분리된 Gmail 본문 조회 한도를 초과했습니다. 메일 수나 검색 범위를 줄여주세요.')
            requests+=1
            response=call('email.attachment.get',{'message_id':message_id,'attachment_id':attachment_id})
            if not isinstance(response,dict) or not isinstance(response.get('data'),str):
                raise ValueError('분리된 Gmail 본문 응답을 확인하지 못했습니다.')
            encoded=response['data']
            if len(encoded)>4*((max_bytes-size+2)//3):
                raise ValueError('분리된 Gmail 본문 조회 크기 한도를 초과했습니다.')
            try:decoded=base64.b64decode(encoded+'='*(-len(encoded)%4),altchars=b'-_',validate=True)
            except (ValueError,UnicodeError):
                raise ValueError('분리된 Gmail 본문 인코딩을 확인하지 못했습니다.') from None
            reported=response.get('size')
            if reported is not None and (type(reported) is not int or reported!=len(decoded)):
                raise ValueError('분리된 Gmail 본문 응답의 크기가 일치하지 않습니다.')
            if len(decoded)>max_bytes-size:
                raise ValueError('분리된 Gmail 본문 조회 크기 한도를 초과했습니다.')
            size+=len(decoded);cache[key]=decoded
        if declared is not None and declared!=len(decoded):
            raise ValueError('분리된 Gmail 본문의 크기가 일치하지 않습니다.')
        return decoded

    return read


def gmail_documents(message,read_body=None):
    """Expose a bounded plain-text view while preserving the Gmail API result port."""
    if not isinstance(message,dict) or not message.get('id') or not isinstance(message.get('payload'),dict):
        return []
    payload=message['payload']
    plain,html=[],[]

    def collect(part):
        headers={h['name'].lower():h['value'] for h in part.get('headers',[])}
        disposition=Message()
        disposition['Content-Disposition']=headers.get('content-disposition','')
        if part.get('filename') or disposition.get_content_disposition()=='attachment':
            return
        mime=part.get('mimeType')
        if mime in {'text/plain','text/html'}:
            (plain if mime=='text/plain' else html).append((part.get('body',{}),headers,mime))
        for child in part.get('parts',[]):
            collect(child)

    def decode(parts):
        decoded_parts=[]
        for body,headers,mime in parts:
            if not isinstance(body,dict):
                raise ValueError('Gmail 메일 본문 응답을 확인하지 못했습니다.')
            encoded=body.get('data')
            if not encoded and 'attachmentId' in body:
                if read_body is None:
                    raise ValueError('분리된 Gmail 본문을 읽을 연결이 필요합니다.')
                content=read_body(message['id'],body)
            elif isinstance(encoded,str):
                try:content=base64.b64decode(encoded+'='*(-len(encoded)%4),altchars=b'-_',validate=True)
                except (ValueError,UnicodeError):content=b''
            else:
                continue
            metadata=Message()
            metadata['Content-Type']=headers.get('content-type',mime)
            try:decoded=content.decode(metadata.get_content_charset() or 'utf-8',errors='replace')
            except LookupError:decoded=content.decode('utf-8',errors='replace')
            decoded_parts.append(decoded)
        return '\n'.join(decoded_parts).strip()

    collect(payload)
    body=decode(plain)
    if not body and html:
        parser=GmailHtmlText()
        parser.feed(decode(html))
        parser.close()
        body=''.join(parser.parts).strip()
    if not body:
        return []
    headers={h['name'].lower():h['value'] for h in payload.get('headers',[])}
    title=headers.get('subject') or '제목 없는 메일'
    text=f"제목: {title}\n보낸 사람: {headers.get('from','')}\n\n{body}"
    text=text if len(text)<=30000 else text[:30000]+'\n[이후 본문 생략]'
    return [{'source_id':'gmail:'+message['id'],'file_id':None,'file_name':title,'page':None,
             'offset':0,'parser_engine':'gmail','derived_result':True,'text':text}]


def read_gmail_messages(call,query=None,max_results=5):
    """Collect a bounded first search page through read-only, account-scoped calls.

    Gmail's newest-first list order is retained (Google's list-messages guide:
    https://developers.google.com/workspace/gmail/api/guides/list-messages).
    A failed read aborts the whole batch rather than
    returning a successful summary of only the messages fetched before failure.
    """
    if type(max_results) is not int or not 1<=max_results<=10:
        raise ValueError('Gmail 본문 조회는 한 번에 1~10개를 지정해주세요.')
    if query is not None and not isinstance(query,str):
        raise ValueError('Gmail 검색 조건은 문자열이어야 합니다.')
    listing=call('email.list',{'query':query,'max_results':max_results})
    messages=listing.get('messages',[]) if isinstance(listing,dict) else None
    if not isinstance(messages,list) or len(messages)>max_results:
        raise ValueError('Gmail 메일 목록 응답을 확인하지 못했습니다.')
    ids=[item.get('id') if isinstance(item,dict) else None for item in messages]
    if any(not isinstance(id,str) or not id for id in ids) or len(set(ids))!=len(ids):
        raise ValueError('Gmail 메일 목록에 유효한 메시지 ID가 없습니다.')
    documents=[];metadata=[];unavailable=[];truncated=[]
    # Keep every selected message in the native summarizer's bounded context.
    per_message=min(8000,40000//len(ids)) if ids else 8000
    read_body=gmail_body_reader(call)
    for id in ids:
        try:
            message=call('email.get',{'message_id':id,'format':'full'})
            if not isinstance(message,dict) or message.get('id')!=id or not isinstance(message.get('payload'),dict):
                raise ValueError('Gmail 메일 본문 응답을 확인하지 못했습니다.')
            chunks=gmail_documents(message,read_body)
        except Exception as error:
            if isinstance(error,GoogleApiError) and error.status in {401,403}:raise
            raise ValueError(f'Gmail 메일 {len(documents)+1}번째 읽기에 실패해 전체 요약을 중단했습니다. 연결 상태를 확인하고 다시 실행해주세요.') from None
        if chunks:
            chunk=chunks[0]
        else:
            unavailable.append(id)
            headers={h['name'].lower():h['value'] for h in message['payload'].get('headers',[])}
            title=headers.get('subject') or '제목 없는 메일'
            chunk={'source_id':'gmail:'+id,'file_id':None,'file_name':title,'page':None,'offset':0,
                   'parser_engine':'gmail','derived_result':True,'body_unavailable':True,
                   'text':f"제목: {title}\n보낸 사람: {headers.get('from','')}\n\n[읽을 수 있는 텍스트 본문이 없습니다.]"}
        if len(chunk['text'])>per_message:
            marker='\n[이후 본문 생략]'
            chunk={**chunk,'text':chunk['text'][:per_message-len(marker)]+marker,'truncated':True}
            truncated.append(id)
        documents.append(chunk)
        metadata.append({key:message[key] for key in ('id','threadId','internalDate') if key in message})
    return {'messages':metadata,'documents':documents,'requested_count':max_results,'returned_count':len(documents),
            'body_unavailable_message_ids':unavailable,'truncated_message_ids':truncated,
            'nextPageToken':listing.get('nextPageToken'),'complete':True}


def timestamp(value):
    clean_text(value,"timestamp")
    dt=datetime.datetime.fromisoformat(value.replace("Z","+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:raise ValueError("Timestamp requires timezone/UTC offset")
    return dt
def event_times(start,end,all_day=False,time_zone=None):
    if not isinstance(all_day,bool):raise ValueError("all_day must be boolean")
    if all_day:
        a=datetime.date.fromisoformat(start);b=datetime.date.fromisoformat(end)
        if b<=a:raise ValueError("All-day end date is exclusive and must follow start date")
        return {"start":{"date":start},"end":{"date":end}}
    a=timestamp(start);b=timestamp(end)
    if b<=a:raise ValueError("Event end must follow start")
    result={"start":{"dateTime":start},"end":{"dateTime":end}}
    if time_zone:
        clean_text(time_zone,"time_zone")
        # Offset-aware dates remain mandatory. Google validates IANA timezone identifiers.
        for v in result.values():v["timeZone"]=time_zone
    return result
def validate_attendees(values):
    if values is None:return None
    return [{"email":v} for v in recipients(values)]
class CalendarAdapter:
    def __init__(self,transport):self.transport=transport
    def __call__(self,operation,arguments):
        op=operation.removeprefix("calendar.")
        handlers={"create_event":self.create_event,"list_events":self.list_events,"get_event":self.get_event,
                  "update_event":self.update_event,"delete_event":self.delete_event}
        if op not in handlers:raise ValueError("Unknown calendar operation")
        return handlers[op](**arguments)
    def path(self,calendar_id,event_id=None):
        p="/calendar/v3/calendars/"+identifier(calendar_id,"calendar_id")+"/events"
        return p if event_id is None else p+"/"+identifier(event_id,"event_id")
    def create_event(self,summary,start,end,calendar_id="primary",all_day=False,time_zone=None,description=None,location=None,attendees=None,notify_attendees=False,event_id=None):
        clean_text(summary,"summary")
        if not isinstance(notify_attendees,bool):raise ValueError("notify_attendees must be boolean")
        if not summary.strip():raise ValueError("Summary required")
        payload={"summary":summary,**event_times(start,end,all_day,time_zone)}
        if description is not None:payload["description"]=description
        if location is not None:payload["location"]=location
        if attendees is not None:payload["attendees"]=validate_attendees(attendees)
        if event_id is not None:
            if not re.fullmatch(r"[a-v0-9]{5,1024}",event_id):raise ValueError("Event ID must be base32hex, 5..1024 characters")
            payload["id"]=event_id
        return self.transport.request("POST",self.path(calendar_id),query={"sendUpdates":"all" if notify_attendees else "none"},payload=payload)
    def list_events(self,calendar_id="primary",time_min=None,time_max=None,max_results=100,page_token=None,query=None):
        if isinstance(max_results,bool) or not isinstance(max_results,int) or not 1<=max_results<=2500:raise ValueError("max_results must be 1..2500")
        if time_min:timestamp(time_min)
        if time_max:timestamp(time_max)
        if time_min and time_max and timestamp(time_max)<=timestamp(time_min):raise ValueError("Invalid time window")
        return self.transport.request("GET",self.path(calendar_id),query={"timeMin":time_min,"timeMax":time_max,
          "maxResults":max_results,"pageToken":page_token,"q":query,"singleEvents":"true","orderBy":"startTime"})
    def get_event(self,event_id,calendar_id="primary"):
        return self.transport.request("GET",self.path(calendar_id,event_id))
    def update_event(self,event_id,changes,calendar_id="primary",notify_attendees=False,etag=None):
        if not isinstance(notify_attendees,bool):raise ValueError("notify_attendees must be boolean")
        if not isinstance(changes,dict) or not changes or set(changes)-{"summary","description","location","start","end","attendees"}:
            raise ValueError("Unsupported/empty patch")
        changes=dict(changes)
        if "summary" in changes:
            clean_text(changes["summary"],"summary")
            if not changes["summary"].strip():raise ValueError("Summary required")
        if "attendees" in changes:changes["attendees"]=validate_attendees(changes["attendees"]) or []
        if "start" in changes or "end" in changes:
            previous=self.get_event(event_id,calendar_id) if not {"start","end"}<=set(changes) else {}
            start=changes.get("start",previous.get("start"));end=changes.get("end",previous.get("end"))
            if not isinstance(start,dict) or not isinstance(end,dict):raise ValueError("Event start/end objects required")
            mode="dateTime" if "dateTime" in start else "date"
            if mode not in end or mode not in start:raise ValueError("Mixed all-day/timed event")
            event_times(start[mode],end[mode],mode=="date",start.get("timeZone"))
        return self.transport.request("PATCH",self.path(calendar_id,event_id),
            query={"sendUpdates":"all" if notify_attendees else "none"},payload=changes,etag=etag)
    def delete_event(self,event_id,calendar_id="primary",notify_attendees=False,etag=None):
        if not isinstance(notify_attendees,bool):raise ValueError("notify_attendees must be boolean")
        self.transport.request("DELETE",self.path(calendar_id,event_id),query={"sendUpdates":"all" if notify_attendees else "none"},etag=etag)
        return {"provider":"google_calendar","event_id":event_id,"status":"delete_acknowledged"}
def configure_context(context,sender_email,token_provider=None,gmail_transport=None,calendar_transport=None):
    """Only attaches providers. This function never sends mail or creates an event."""
    token_provider=token_provider or EnvironmentAccessToken()
    gmail_transport=gmail_transport or GoogleJsonTransport(token_provider,"https://gmail.googleapis.com")
    calendar_transport=calendar_transport or GoogleJsonTransport(token_provider,"https://www.googleapis.com")
    context.connectors["gmail"]=GmailAdapter(gmail_transport,sender_email,context.root)
    context.connectors["google_calendar"]=CalendarAdapter(calendar_transport)
    return context

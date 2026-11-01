"""Register concrete Google operations in the expansion runtime (no network on import)."""
from .google_adapters import prepare_message
def register_google_tools(tool,invoke_connector,registry):
    @tool("email.prepare","Build UTF-8 MIME and Base64URL message without sending it.")
    def email_prepare(sender,to,subject,body,context,cc=None,bcc=None,attachments=None):
        return prepare_message(sender,to,subject,body,cc,bcc,attachments,context.root)
    @tool("email.send","Send a Gmail message using a configured Google account.")
    def email_send(to,subject,body,context,cc=None,bcc=None,attachments=None):
        return invoke_connector("gmail","email.send",{"to":to,"subject":subject,"body":body,"cc":cc,"bcc":bcc,"attachments":attachments},context)
    @tool("email.list","List Gmail message IDs, search query and pagination token.")
    def email_list(context,query=None,max_results=20,page_token=None):
        return invoke_connector("gmail","email.list",{"query":query,"max_results":max_results,"page_token":page_token},context)
    @tool("email.get","Read a specified Gmail message.")
    def email_get(message_id,context,format="full"):
        return invoke_connector("gmail","email.get",{"message_id":message_id,"format":format},context)
    @tool("email.attachment.get","Retrieve a specified Gmail attachment as provider Base64URL data.")
    def attachment_get(message_id,attachment_id,context):
        return invoke_connector("gmail","email.attachment.get",{"message_id":message_id,"attachment_id":attachment_id},context)
    @tool("calendar.create_event","Create a Google Calendar event with explicit start/end and timezone offsets.")
    def create_event(summary,start,end,context,calendar_id="primary",all_day=False,time_zone=None,description=None,location=None,attendees=None,notify_attendees=False,event_id=None):
        return invoke_connector("google_calendar","calendar.create_event",{"summary":summary,"start":start,"end":end,"calendar_id":calendar_id,
          "all_day":all_day,"time_zone":time_zone,"description":description,"location":location,"attendees":attendees,
          "notify_attendees":notify_attendees,"event_id":event_id},context)
    @tool("calendar.list_events","List calendar events with time bounds and pagination.")
    def list_events(context,calendar_id="primary",time_min=None,time_max=None,max_results=100,page_token=None,query=None):
        return invoke_connector("google_calendar","calendar.list_events",{"calendar_id":calendar_id,"time_min":time_min,"time_max":time_max,
          "max_results":max_results,"page_token":page_token,"query":query},context)
    @tool("calendar.get_event","Read a Google Calendar event by ID.")
    def get_event(event_id,context,calendar_id="primary"):
        return invoke_connector("google_calendar","calendar.get_event",{"event_id":event_id,"calendar_id":calendar_id},context)
    @tool("calendar.update_event","Patch supported event fields; optional ETag concurrency control.")
    def update_event(event_id,changes,context,calendar_id="primary",notify_attendees=False,etag=None):
        return invoke_connector("google_calendar","calendar.update_event",{"event_id":event_id,"changes":changes,"calendar_id":calendar_id,
          "notify_attendees":notify_attendees,"etag":etag},context)
    @tool("calendar.delete_event","Delete a specified Google Calendar event; optional ETag.")
    def delete_event(event_id,context,calendar_id="primary",notify_attendees=False,etag=None):
        return invoke_connector("google_calendar","calendar.delete_event",{"event_id":event_id,"calendar_id":calendar_id,
          "notify_attendees":notify_attendees,"etag":etag},context)
    for id in ["email.send","email.list","email.get","email.attachment.get","calendar.create_event","calendar.list_events","calendar.get_event","calendar.update_event","calendar.delete_event"]:
        registry[id]["provider_required"]=True
        registry[id]["provider"]="gmail" if id.startswith("email.") else "google_calendar"
        registry[id]["side_effect"]=id in {"email.send","calendar.create_event","calendar.update_event","calendar.delete_event"}
    registry["email.prepare"]["side_effect"]=False

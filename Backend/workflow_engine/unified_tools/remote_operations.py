"""Explicit remote-link HEAD provider. Never follows a redirect to an unapproved host."""
import ipaddress,socket,urllib.error,urllib.request
from urllib.parse import urlsplit
from workflow_engine.unified_tools.local_operations import links
def validate_url(url,allowed_hosts,resolve=True):
 u=urlsplit(url)
 if u.scheme not in {"https","http"} or not u.hostname or u.username or u.password or u.hostname not in allowed_hosts:raise ValueError("Remote link host must be explicitly allowed")
 if resolve:
  addresses=socket.getaddrinfo(u.hostname,u.port or (443 if u.scheme=="https" else 80),type=socket.SOCK_STREAM)
  if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ValueError("Non-public network targets are not allowed")
 return url
class HTTPHeadProvider:
 def __init__(self,allowed_hosts,credential_resolver=None):
  if not allowed_hosts:raise ValueError("An explicit host allowlist is required")
  self.allowed_hosts=set(allowed_hosts);self.credential_resolver=credential_resolver
 def __call__(self,operation,arguments):
  if operation!="head":raise ValueError("HEAD only")
  url=validate_url(arguments["url"],self.allowed_hosts);timeout=arguments["timeout_seconds"]
  if not 1<=timeout<=60:raise ValueError("Timeout must be 1..60 seconds")
  provider=self
  class NoRedirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,req,fp,code,msg,headers,newurl):raise ValueError("Redirect requires explicit target validation")
  headers={"User-Agent":"A4B-LinkCheck/1"}
  if arguments.get("credential_ref"):
   if not self.credential_resolver:raise ValueError("Credential reference resolver is not configured")
   headers.update(self.credential_resolver(arguments["credential_ref"],url))
  req=urllib.request.Request(url,headers=headers,method="HEAD")
  try:
   with urllib.request.build_opener(NoRedirect()).open(req,timeout=timeout) as r:return {"status_code":r.status,"reachable":200<=r.status<400}
  except urllib.error.HTTPError as e:return {"status_code":e.code,"reachable":False}
  except urllib.error.URLError:return {"status_code":None,"reachable":False,"error":"connection_failed"}
def check_remote_targets(document,timeout_seconds,context,credential_ref=None):
 if type(timeout_seconds) not in (int,float) or not 1<=timeout_seconds<=60:raise ValueError("Timeout must be 1..60 seconds")
 provider=context.connectors.get("http_head")
 if provider is None:raise RuntimeError("Configure Context.connectors.http_head")
 checks=[]
 for target in links(document):
  if urlsplit(target).scheme not in {"https","http"}:continue
  response=provider("head",{"url":target,"timeout_seconds":timeout_seconds,"credential_ref":credential_ref})
  if not isinstance(response,dict) or type(response.get("reachable"))!=bool:raise ValueError("Invalid HEAD provider response")
  checks.append({"target":target,"passed":response["reachable"],"status_code":response.get("status_code")})
 return {"review":{"checks":checks,"passed":all(c["passed"] for c in checks)}}

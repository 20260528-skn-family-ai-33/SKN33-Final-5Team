"""Operation-aware call scores and observed execution/revision timing.
Call F1 counts tool/operation multiplicity; argument F1 is a separate exact-JSON metric.
"""
from collections import Counter
from dataclasses import dataclass
import hashlib,json,time

def call_pair(call,resolver=None,strict=False):
 if not isinstance(call,dict) or not isinstance(call.get("tool"),str):raise ValueError("Each call needs a tool ID")
 tool=call["tool"];op=call.get("operation")
 if resolver:
  try:
   t,o=resolver(tool,op);return t["id"],o["id"]
  except ValueError:
   if strict:raise ValueError("Reference calls need a known tool and explicit valid operation") from None
 if op is not None and not isinstance(op,str):raise ValueError("Operation must be a string")
 if strict and not op:raise ValueError("Reference calls need an operation")
 return tool,op if op is not None else "<unspecified>"

def call_f1(predicted,expected,resolver=None):
 """Multiset F1 of (canonical tool ID, operation ID); same tool/wrong op is wrong."""
 if not isinstance(predicted,list) or not isinstance(expected,list):raise ValueError("Calls must be lists")
 p=Counter(call_pair(c,resolver) for c in predicted)
 g=Counter(call_pair(c,resolver,strict=True) for c in expected)
 tp=sum((p & g).values());fp=sum(p.values())-tp;fn=sum(g.values())-tp
 precision=tp/(tp+fp) if tp+fp else (1.0 if not g else 0.0)
 recall=tp/(tp+fn) if tp+fn else (1.0 if not p else 0.0)
 f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 1.0
 return {"unit":"(tool, operation) call multiset","tp":tp,"fp":fp,"fn":fn,
         "precision":precision,"recall":recall,"f1":f1,
         "predicted_count":sum(p.values()),"expected_count":sum(g.values()),
         "arguments_evaluated":False,"order_evaluated":False}


def _json_value(value):
 """Exact JSON comparison: object key order ignored; array order and scalar types retained."""
 return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(",",":"))

def _args(call):
 value=call.get("args")
 if not isinstance(value,dict) or any(not isinstance(k,str) for k in value):
  raise ValueError("Reference and observed args must be JSON objects with string keys")
 _json_value(value)
 return value

def reference_signature(calls,resolver=None):
 return Counter((call_pair(c,resolver,True),"args" in c,_json_value(_args(c)) if "args" in c else None) for c in calls)

def _f1(tp,predicted,expected,unit):
 fp=predicted-tp;fn=expected-tp
 precision=tp/predicted if predicted else (1.0 if not expected else 0.0)
 recall=tp/expected if expected else (1.0 if not predicted else 0.0)
 return {"unit":unit,"tp":tp,"fp":fp,"fn":fn,"precision":precision,"recall":recall,
         "f1":2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 1.0,
         "predicted_count":predicted,"expected_count":expected}

def _best_pairs(weights):
 """Rectangular Hungarian assignment; rows <= columns, maximum total integer weight."""
 n=len(weights);m=len(weights[0]) if n else 0
 if not n:return []
 u=[0]*(n+1);v=[0]*(m+1);p=[0]*(m+1);way=[0]*(m+1)
 for i in range(1,n+1):
  p[0]=i;j0=0;minv=[float("inf") ]*(m+1);used=[False]*(m+1)
  while True:
   used[j0]=True;i0=p[j0];delta=float("inf");j1=0
   for j in range(1,m+1):
    if not used[j]:
     cur=-weights[i0-1][j-1]-u[i0]-v[j]
     if cur<minv[j]:minv[j]=cur;way[j]=j0
     if minv[j]<delta:delta=minv[j];j1=j
   for j in range(m+1):
    if used[j]:u[p[j]]+=delta;v[j]-=delta
    else:minv[j]-=delta
   j0=j1
   if p[j0]==0:break
  while True:
   j1=way[j0];p[j0]=p[j1];j0=j1
   if j0==0:break
 return [(p[j]-1,j-1) for j in range(1,m+1) if p[j]]

def argument_f1(predicted,expected,resolver=None):
 """Pair whole calls one-to-one within (tool, operation), then score named JSON values.
 All reference calls must explicitly provide args; missing labels are never scored as {}.
 """
 if not isinstance(predicted,list) or not isinstance(expected,list):raise ValueError("Calls must be lists")
 for c in expected:
  call_pair(c,resolver,True)
  if "args" in c:_args(c)
 if any("args" not in c for c in expected):return None
 pa=[_args(c) for c in predicted];ga=[_args(c) for c in expected]
 pg={};gg={}
 for i,c in enumerate(predicted):pg.setdefault(call_pair(c,resolver),[]).append(i)
 for i,c in enumerate(expected):gg.setdefault(call_pair(c,resolver,True),[]).append(i)
 name_tp=value_tp=exact=0
 for key in pg.keys() & gg.keys():
  ps=pg[key];gs=gg[key];flip=len(ps)>len(gs)
  left=gs if flip else ps;right=ps if flip else gs
  max_names=sum(len(pa[i]) for i in ps)+sum(len(ga[i]) for i in gs)+1
  cbase=min(len(ps),len(gs))+1;details=[];weights=[]
  for a in left:
   detail=[];weight=[]
   for b in right:
    x,y=(pa[b],ga[a]) if flip else (pa[a],ga[b])
    names=len(x.keys() & y.keys())
    values=sum(_json_value(x[k])==_json_value(y[k]) for k in x.keys() & y.keys())
    same=int(_json_value(x)==_json_value(y))
    detail.append((names,values,same));weight.append((values*max_names+names)*cbase+same)
   details.append(detail);weights.append(weight)
  for i,j in _best_pairs(weights):
   names,values,same=details[i][j];name_tp+=names;value_tp+=values;exact+=same
 pred=sum(map(len,pa));gold=sum(map(len,ga));denominator=max(len(pa),len(ga))
 return {"argument_name_f1":_f1(name_tp,pred,gold,"top-level argument name per paired call"),
         "argument_value_f1":_f1(value_tp,pred,gold,"(argument name, exact JSON value) per paired call"),
         "argument_exact_match_rate":exact/denominator if denominator else 1.0,
         "exact_call_count":exact,"alignment":"one-to-one within (tool, operation); maximize exact values, then names, then complete argument matches",
         "comparison":"top-level keys; nested JSON compared in full; object key order ignored; array order and scalar types retained; no formula equivalence"}

@dataclass
class ExecutionTracker:
 first_started_at_ns:int|None=None
 first_started_perf_ns:int|None=None
 last_fingerprint:str|None=None
 attempt_count:int=0
 repair_count:int=0
 retry_count:int=0
 expected_calls:list|None=None

 @classmethod
 def from_state(cls,state):
  if not isinstance(state,dict):raise ValueError("Invalid execution tracking state")
  required={"first_started_at_ns","last_fingerprint","attempt_count","repair_count","retry_count","expected_calls"}
  if set(state)-{"first_started_perf_ns"}!=required:raise ValueError("Incomplete execution tracking state")
  if state.get("first_started_perf_ns") is not None and (type(state["first_started_perf_ns"]) is not int or state["first_started_perf_ns"]<0):raise ValueError("Invalid monotonic clock")
  for key in ("attempt_count","repair_count","retry_count"):
   if type(state[key]) is not int or state[key]<0:raise ValueError("Invalid tracking counter")
  if state["attempt_count"]!=state["repair_count"]+state["retry_count"]+1:raise ValueError("Inconsistent tracking counters")
  if type(state["first_started_at_ns"]) is not int or state["first_started_at_ns"]<=0:raise ValueError("Invalid tracking clock")
  if not isinstance(state["last_fingerprint"],str) or len(state["last_fingerprint"])!=64:raise ValueError("Invalid tracking fingerprint")
  if state["expected_calls"] is not None and not isinstance(state["expected_calls"],list):raise ValueError("Invalid reference calls")
  return cls(**state)

 def state(self):
  return {"first_started_at_ns":self.first_started_at_ns,"first_started_perf_ns":self.first_started_perf_ns,"last_fingerprint":self.last_fingerprint,
          "attempt_count":self.attempt_count,"repair_count":self.repair_count,"retry_count":self.retry_count,
          "expected_calls":self.expected_calls}

 def begin(self,workflow,inputs,expected_calls=None,resolver=None):
  supplied=json.loads(json.dumps(expected_calls)) if expected_calls is not None else None
  if supplied is not None:
   if not isinstance(supplied,list):raise ValueError("Expected calls must be a list")
   # Validate the oracle before any tool can cause side effects.
   reference_signature(supplied,resolver)
  if self.attempt_count and supplied is not None:
   if self.expected_calls is None or reference_signature(supplied,resolver)!=reference_signature(self.expected_calls,resolver):
    raise ValueError("Reference calls cannot change during an execution-repair chain")
  if not self.attempt_count:self.expected_calls=supplied
  fingerprint=hashlib.sha256(json.dumps({"workflow":workflow,"inputs":inputs},sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(",",":")).encode()).hexdigest()
  now=time.time_ns();started=time.perf_counter_ns()
  if self.first_started_at_ns is None:
   self.first_started_at_ns=now;self.first_started_perf_ns=started
  if self.last_fingerprint is not None:
   if fingerprint==self.last_fingerprint:self.retry_count+=1
   else:self.repair_count+=1
  self.last_fingerprint=fingerprint;self.attempt_count+=1
  return started

 def finish(self,result,workflow,calls,started,resolver=None):
  monotonic_now=time.perf_counter_ns()
  duration=(monotonic_now-started)/1_000_000
  valid_monotonic=self.first_started_perf_ns is not None and monotonic_now>=self.first_started_perf_ns
  elapsed=(monotonic_now-self.first_started_perf_ns)/1_000_000 if valid_monotonic else (time.time_ns()-self.first_started_at_ns)/1_000_000
  elapsed=max(duration,elapsed,0)
  reference=self.expected_calls
  try:planned=call_f1(workflow["nodes"],reference,resolver) if reference is not None else None
  except (ValueError,KeyError,TypeError):planned=None
  actual=call_f1(calls,reference,resolver) if reference is not None else None
  arguments=argument_f1(calls,reference,resolver) if reference is not None else None
  return {"duration_ms":round(max(0,duration),3),
          "completion_time_ms":round(elapsed,3) if result.get("status")=="completed" else None,
          "elapsed_since_first_attempt_ms":round(elapsed,3),
          "attempt_count":self.attempt_count,"repair_count":self.repair_count,"retry_count":self.retry_count,
          "call_f1":actual,"planned_call_f1":planned,
          "argument_name_f1":arguments["argument_name_f1"] if arguments is not None else None,
          "argument_value_f1":arguments["argument_value_f1"] if arguments is not None else None,
          "argument_exact_match_rate":arguments["argument_exact_match_rate"] if arguments is not None else None,
          "arguments_evaluated":arguments is not None,"argument_evaluation":arguments,
          "argument_evaluation_unavailable_reason":None if arguments is not None else "reference_calls_missing_or_args_not_labeled",
          "reference_calls_provided":reference is not None,
          "completion_clock":"perf_counter_ns" if valid_monotonic else "wall_clock_fallback",
          "timing_scope":"duration: current dispatcher call; completion: first attempt to success, including time between repairs",
          "repair_scope":"workflow/input JSON changed versus immediately previous attempt; unchanged retries counted separately",
          "overall_task_quality_verified":False}

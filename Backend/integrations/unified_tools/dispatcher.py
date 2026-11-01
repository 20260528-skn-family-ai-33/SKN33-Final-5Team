"""Shared canonical tool dispatcher. No ML training or implicit external actions.
The legacy backend can be attached through an explicit trusted callback.
"""
from pathlib import Path
import sys,json,inspect,copy
ROOT=Path(__file__).resolve().parents[3]
from .expansion import runtime
from .local_operations import OPERATIONS as LOCAL_OPERATIONS
from . import file_operations
from .model_operations import SPECS as MODEL_OPERATIONS, perform as perform_model
from .execution_metrics import ExecutionTracker
from .parser_errors import DocumentParseError,is_parser_exception,argument_file_format
from .remote_operations import check_remote_targets
VERSION="a4b-unified-tools-v2"
CATALOG=Path(__file__).resolve().parent/"catalog.json"
class ToolUnavailable(RuntimeError):pass
class WorkflowValidationError(ValueError):
    def __init__(self,code,message):
        super().__init__(message);self.code=code
class InvalidReferencePath(ValueError):
    def __init__(self,reference,path):
        super().__init__("Referenced output path is missing or out of range")
        self.reference=reference;self.path=path
def catalog():return json.loads(CATALOG.read_text("utf-8"))
def resolve(tool_id,operation=None):
    data=catalog()
    if operation is None and tool_id in data["aliases"]:
        target=data["aliases"][tool_id];tool_id=target["tool_id"];operation=target["operation"]
    tools={t["id"]:t for t in data["tools"]}
    if tool_id not in tools:raise ValueError("Unknown tool ID")
    t=tools[tool_id]
    ops={o["id"]:o for o in t["operations"]}
    if operation is None:
        if len(ops)!=1:raise ValueError("Operation must be explicit")
        operation=next(iter(ops))
    if operation not in ops:raise ValueError("Unknown operation")
    return t,ops[operation]
def preflight(tool_id,operation=None,context=None):
    t,o=resolve(tool_id,operation)
    status=o["status"]
    if (t["id"],o["id"]) in LOCAL_OPERATIONS:status="local_ready"
    if (t["id"],o["id"])==("check_document_links","check_remote_targets"):
        status="configured_provider_not_live_verified" if context is not None and "http_head" in context.connectors else "external_connection_required"
    if o.get("runtime_id"):
        e=runtime.TOOLS[o["runtime_id"]]
        if o["runtime_id"]=="connector.invoke":status="external_connection_required"
        elif e.get("provider_required"):
            provider=e["provider"]
            if context is None or provider not in context.connectors:status="external_connection_required"
            else:status="configured_provider_not_live_verified"
        else:status="local_ready"
    return {"tool_id":t["id"],"operation":o["id"],"status":status,"requirements":o["requirements"],
      "callable":status in {"local_ready","configured_provider_not_live_verified"}}
def execute(tool_id,operation,arguments,context=None,native_backend=None,model_providers=None):
    t,o=resolve(tool_id,operation)
    key=(t["id"],o["id"])
    if key in LOCAL_OPERATIONS:
        fn=LOCAL_OPERATIONS[key]["function"];kwargs=dict(arguments)
        if "context" in inspect.signature(fn).parameters:kwargs["context"]=context
        inspect.signature(fn).bind(**kwargs)
        try:return fn(**kwargs)
        except DocumentParseError:raise
        except Exception as error:
            # Some parsers defer XML/page parsing until after the loader returns.
            if is_parser_exception(error):
                raise DocumentParseError(argument_file_format(arguments),type(error).__name__) from error
            raise
    if key in MODEL_OPERATIONS:
        provider=(model_providers or {}).get(t["id"]) or (model_providers or {}).get("*")
        if provider is None:raise ToolUnavailable("Model provider is not configured")
        return perform_model(t["id"],o["id"],arguments,provider,context)
    if key==("check_document_links","check_remote_targets"):
        return check_remote_targets(**arguments,context=context)
    if o.get("runtime_id"):return runtime.invoke(o["runtime_id"],arguments,context)
    if o.get("implementation")=="keyword_search":
        docs=arguments["documents"];keywords=arguments["keywords"];limit=arguments.get("top_k",3)
        if not isinstance(limit,int) or isinstance(limit,bool) or limit<1:raise ValueError("Positive top_k required")
        if not keywords or any(not isinstance(k,str) or not k.strip() for k in keywords):raise ValueError("Nonempty keyword list required")
        hits=[]
        for i,d in enumerate(docs):
            score=sum(k.casefold() in d["text"].casefold() for k in keywords)
            if score:hits.append((score,i,{**d,"score":score}))
        return [d for _,_,d in sorted(hits,key=lambda x:(-x[0],x[1]))[:limit]]
    if o.get("implementation")=="finish":
        return {"status":"completed","message":arguments.get("message","처리가 완료되었습니다."),"result":arguments.get("result")}
    if o.get("implementation")=="ask_user":
        questions=arguments["questions"]
        if not questions or any(not isinstance(q,str) or not q.strip() for q in questions):raise ValueError("Explicit questions required")
        return {"status":"needs_clarification","questions":questions,"missing_fields":arguments.get("missing_fields",[]),
                "execution_paused_by_backend":False}
    if o.get("implementation")=="export_serialize":
        value=arguments["value"];fmt=arguments["format"];filename=arguments["filename"]
        if fmt=="json":return runtime.invoke("file.write_json",{"path":filename,"value":value},context)
        if fmt=="csv":
            if not isinstance(value,list) or any(not isinstance(r,dict) for r in value):raise ValueError("CSV requires record rows")
            cols=list(dict.fromkeys(c for r in value for c in r))
            return runtime.invoke("file.write_csv",{"path":filename,"rows":value,"columns":cols},context)
        raise ValueError("This executor supports csv/json only")
    if o.get("backend_tool") and native_backend is not None:
        return native_backend(o["backend_tool"],arguments)
    if o["status"]=="model_connection_required":
        fn=(model_providers or {}).get(t["id"])
        if fn:return fn(o["id"],arguments)
    raise ToolUnavailable(json.dumps(preflight(t["id"],o["id"],context),ensure_ascii=False))
def _run(workflow,inputs,context=None,native_backend=None,model_providers=None,_calls=None,_progress=None):
    if not isinstance(workflow,dict) or not isinstance(workflow.get("nodes"),list) or not workflow["nodes"]:
        raise WorkflowValidationError("invalid_workflow","Workflow needs a nonempty node list")
    nodes=workflow["nodes"]
    if not isinstance(inputs,dict):raise WorkflowValidationError("invalid_workflow_inputs","Inputs must be an object")
    for n in nodes:
        if not isinstance(n,dict) or not isinstance(n.get("id"),str) or not n["id"] or not isinstance(n.get("tool"),str):
            raise WorkflowValidationError("invalid_workflow_node","Each node needs string id and tool")
        if not isinstance(n.get("args",{}),dict) or not isinstance(n.get("depends_on",[]),list) or any(not isinstance(x,str) for x in n.get("depends_on",[])):
            raise WorkflowValidationError("invalid_workflow_node","Node args must be an object and depends_on a string list")
    lookup={n["id"]:n for n in nodes}
    if len(lookup)!=len(nodes):raise WorkflowValidationError("duplicate_node_ids","Duplicate node IDs")
    def refs(v):
        if isinstance(v,dict):
            if "$ref" in v and set(v)<= {"$ref","$path"}:
                if not isinstance(v["$ref"],str):raise WorkflowValidationError("invalid_reference","Reference node ID must be a string")
                return {v["$ref"]}
            return set().union(*(refs(x) for x in v.values())) if v else set()
        if isinstance(v,list):return set().union(*(refs(x) for x in v)) if v else set()
        return set()
    deps={n["id"]:refs(n.get("args",{}))|set(n.get("depends_on",[])) for n in nodes}
    if any(d not in lookup for ds in deps.values() for d in ds):raise WorkflowValidationError("unknown_dependency","Unknown dependency")
    order=[];pending=set(lookup)
    while pending:
        ready=sorted(n for n in pending if deps[n]<=set(order))
        if not ready:raise WorkflowValidationError("workflow_cycle","Workflow cycle")
        order.extend(ready);pending.difference_update(ready)
    # A canonical finish operation terminates the whole sequential run. Nodes after
    # it are neither preflighted nor invoked; their missing inputs/providers cannot block finish.
    execution_order=order
    for index,node_id in enumerate(order):
        try:_,op=resolve(lookup[node_id]["tool"],lookup[node_id].get("operation"))
        except ValueError:continue
        if op.get("implementation")=="finish":
            execution_order=order[:index+1];break
    execution_nodes=[lookup[node_id] for node_id in execution_order]
    def input_keys(value):
        if isinstance(value,dict):
            if set(value)=={"$input"}:return {value["$input"]}
            return set().union(*(input_keys(v) for v in value.values())) if value else set()
        if isinstance(value,list):return set().union(*(input_keys(v) for v in value)) if value else set()
        return set()
    for n in execution_nodes:
        missing=input_keys(n.get("args",{}))-set(inputs)
        if missing:return {"status":"needs_clarification","blocked_node":n["id"],"missing_fields":sorted(missing),"outputs":{},"steps":[]}
    # Check all operations before running a file write/provider call.
    for n in execution_nodes:
        try:t,o=resolve(n["tool"],n.get("operation"))
        except ValueError as e:return {"status":"needs_tool_proposal","blocked_node":n["id"],"requested_tool":n["tool"],"reason":str(e),"outputs":{},"steps":[]}
        state=preflight(t["id"],o["id"],context)
        callback_ready=(o.get("backend_tool") and native_backend is not None) or (o["status"]=="model_connection_required" and (t["id"] in (model_providers or {}) or ((t["id"],o["id"]) in MODEL_OPERATIONS and "*" in (model_providers or {}))))
        required=MODEL_OPERATIONS.get((t["id"],o["id"]), (None,None,[]))[2]
        required=list(required) + ({"keyword_search":["documents","keywords"],"ask_user":["questions"],"export_serialize":["value","format","filename"]}.get(o.get("implementation"),[]))
        missing=set(required)-set(n.get("args",{}))
        if missing:return {"status":"needs_clarification","blocked_node":n["id"],"missing_fields":sorted(missing),"outputs":{},"steps":[]}
        if o["status"]=="unimplemented":return {"status":"needs_tool_implementation","blocked_node":n["id"],"tool":t["id"],"operation":o["id"],"outputs":{},"steps":[]}
        if not state["callable"] and not callback_ready:return {"status":"needs_connection","blocked_node":n["id"],"requirements":state["requirements"],"outputs":{},"steps":[]}
        local=LOCAL_OPERATIONS.get((t["id"],o["id"]))
        if local or o.get("runtime_id"):
            fn=local["function"] if local else runtime.TOOLS[o["runtime_id"]]["function"];a=dict(n.get("args",{}))
            if "context" in inspect.signature(fn).parameters:a["context"]=context
            try:inspect.signature(fn).bind(**a)
            except TypeError as e:return {"status":"needs_clarification","blocked_node":n["id"],"questions":["필수 입력을 확인해 주세요: "+str(e)],"outputs":{},"steps":[]}
    outputs={};steps=[]
    if _progress is not None:_progress.update(outputs=outputs,steps=steps)
    def bind(v):
        if isinstance(v,dict):
            if "$ref" in v and set(v)<= {"$ref","$path"}:
                reference=v["$ref"];keys=v.get("$path",[])
                if not isinstance(keys,list) or reference not in outputs:raise InvalidReferencePath(reference,keys)
                value=outputs[reference]
                for key in keys:
                    if isinstance(value,(list,tuple)):
                        if type(key) is not int or not 0<=key<len(value):raise InvalidReferencePath(reference,keys)
                    elif isinstance(value,dict):
                        if not isinstance(key,str) or key not in value:raise InvalidReferencePath(reference,keys)
                    else:raise InvalidReferencePath(reference,keys)
                    value=value[key]
                return value
            if set(v)=={"$input"}:return inputs[v["$input"]]
            return {k:bind(x) for k,x in v.items()}
        if isinstance(v,list):return [bind(x) for x in v]
        return v
    for id in execution_order:
        n=lookup[id]
        if _progress is not None:_progress["blocked_node"]=id
        try:
            arguments=bind(n.get("args",{}))
            selected,op=resolve(n["tool"],n.get("operation"))
            call={"node_id":id,"tool":selected["id"],"operation":op["id"],"args":copy.deepcopy(arguments),"status":"attempted"}
            if _calls is not None:_calls.append(call)
            out=execute(n["tool"],n.get("operation"),arguments,context,native_backend,model_providers)
            call["status"]="completed"
        except DocumentParseError as error:
            return {"status":"invalid_input","blocked_node":id,
                    "error":{"code":"document_parse_failed","file_format":error.file_format,
                             "exception_type":error.exception_type,"message":"문서를 읽을 수 없습니다. 파일 손상 또는 지원 형식을 확인해 주세요."},
                    "questions":["문서가 정상적으로 열리는지 확인한 뒤 다시 업로드해 주세요."],"outputs":outputs,"steps":steps}
        except (IsADirectoryError,NotADirectoryError):
            return {"status":"invalid_input","blocked_node":id,"error":{"code":"expected_file","message":"A file is required; check the selected path."},"outputs":outputs,"steps":steps}
        except FileNotFoundError:
            return {"status":"needs_clarification","blocked_node":id,"error":{"code":"file_not_found","message":"입력 파일을 찾을 수 없습니다."},"questions":["파일을 업로드하거나 작업 폴더 안의 파일 경로를 확인해 주세요."],"outputs":outputs,"steps":steps}
        except FileExistsError:
            return {"status":"needs_clarification","blocked_node":id,"error":{"code":"output_file_exists","message":"결과 파일이 이미 있습니다. 다른 파일 이름을 지정해 주세요."},"outputs":outputs,"steps":steps}
        except InvalidReferencePath as e:
            return {"status":"invalid_input","blocked_node":id,"error":{"code":"invalid_reference_path","reference":e.reference,"path":e.path,"message":str(e)},"outputs":outputs,"steps":steps}
        except IndexError:
            return {"status":"invalid_input","blocked_node":id,"error":{"code":"index_out_of_range","message":"입력 배열의 위치가 유효 범위를 벗어났습니다."},"outputs":outputs,"steps":steps}
        except PermissionError:
            return {"status":"execution_failed","blocked_node":id,"error":{"code":"file_access_denied","message":"파일에 접근할 수 없습니다."},"outputs":outputs,"steps":steps}
        except (ToolUnavailable,runtime.MissingIntegration) as e:
            return {"status":"needs_connection","blocked_node":id,"reason":str(e),"outputs":outputs,"steps":steps}
        except ImportError as e:return {"status":"needs_dependencies","blocked_node":id,"dependency":getattr(e,"name",None),"outputs":outputs,"steps":steps}
        except RuntimeError as e:return {"status":"execution_failed","blocked_node":id,"reason":str(e),"outputs":outputs,"steps":steps}
        except KeyError as e:return {"status":"needs_clarification","blocked_node":id,"missing_field":str(e),"questions":["누락된 입력을 지정해 주세요: "+str(e)],"outputs":outputs,"steps":steps}
        except (ValueError,TypeError,ArithmeticError,SyntaxError) as e:return {"status":"invalid_input","blocked_node":id,"reason":str(e),"outputs":outputs,"steps":steps}
        outputs[id]=out;steps.append({"node_id":id,"tool":n["tool"],"operation":n.get("operation"),"status":"completed"})
        if op.get("implementation")=="finish":
            skipped=[{"node_id":x,"status":"skipped","reason":"workflow_completed"} for x in order[len(execution_order):]]
            return {"status":"completed","terminated_by":id,"outputs":outputs,"steps":steps,"skipped_nodes":skipped}
        if isinstance(out,dict) and out.get("status")=="needs_clarification":
            return {"status":"needs_clarification","outputs":outputs,"steps":steps}
    return {"status":"completed","outputs":outputs,"steps":steps}


def run(workflow,inputs,context=None,native_backend=None,model_providers=None,*,expected_calls=None,tracker=None):
    tracker=tracker if tracker is not None else ExecutionTracker()
    started=tracker.begin(workflow,inputs,expected_calls,resolve)
    calls=[];progress={"outputs":{},"steps":[]}
    try:result=_run(workflow,inputs,context,native_backend,model_providers,calls,progress)
    except WorkflowValidationError as e:
        result={"status":"invalid_input","error":{"code":e.code,"message":str(e)},"outputs":{},"steps":[]}
    except Exception as error:
        # Last boundary for ordinary execution failures. Do not catch BaseException
        # (interrupt/exit), or expose parser/provider messages containing private paths.
        result={"status":"execution_failed","error":{"code":"unexpected_execution_error",
                "exception_type":type(error).__name__,"message":"도구 실행 중 오류가 발생했습니다. 실행 기록을 확인해 주세요."},
                "outputs":progress["outputs"],"steps":progress["steps"]}
        if "blocked_node" in progress:result["blocked_node"]=progress["blocked_node"]
    for call in calls:
        if call["status"]=="attempted":call["status"]="failed"
    result["calls"]=calls
    result["metrics"]=tracker.finish(result,workflow,calls,started,resolve)
    result["tracking_state"]=tracker.state()
    return result

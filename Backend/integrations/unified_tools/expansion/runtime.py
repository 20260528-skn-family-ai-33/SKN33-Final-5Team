"""Executable expansion prototype. Independent of the production A4B registry.
No source dataset code is executed. Network/email/calendar providers must be injected.
"""
from __future__ import annotations
import ast,base64,csv,datetime,hashlib,inspect,io,json,math,operator
from dataclasses import dataclass,field
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,urlencode,parse_qsl
TOOLS={}
def tool(id,description):
    def decorate(fn):
        TOOLS[id]={"function":fn,"description":description}
        return fn
    return decorate
def number(v):
    if isinstance(v,bool) or v is None:raise ValueError("A finite number is required")
    d=Decimal(str(v))
    if not d.is_finite():raise ValueError("Non-finite number")
    return d
def plain(v):
    if isinstance(v,Decimal):
        out=int(v) if v==v.to_integral_value() else float(v)
        if isinstance(out,float) and not math.isfinite(out):raise ValueError("Numeric overflow")
        return out
    return v
def compare(a,op,b):
    fn={"eq":operator.eq,"ne":operator.ne,"gt":operator.gt,"ge":operator.ge,"lt":operator.lt,"le":operator.le}.get(op)
    if not fn:raise ValueError("Unknown comparison")
    return fn(a,b)
@tool("logic.compare","Compare values with explicit eq/ne/gt/ge/lt/le semantics.")
def logic_compare(left,op,right):return compare(left,op,right)
@tool("table.filter","Filter table rows by explicit predicates and AND/OR.")
def table_filter(rows,predicates,combine="and"):
    if combine not in {"and","or"}:raise ValueError("combine must be and/or")
    if not predicates:raise ValueError("Empty predicates require clarification")
    return [dict(r) for r in rows if (all if combine=="and" else any)(compare(r[p["column"]],p["op"],r[p["other_column"]] if "other_column" in p else p["value"]) for p in predicates)]
@tool("table.sort","Sort rows by explicit keys without altering values.")
def table_sort(rows,keys):
    result=[dict(r) for r in rows]
    for k in reversed(keys):result.sort(key=lambda r:r[k["column"]],reverse=k.get("descending",False))
    return result
@tool("table.aggregate","Grouped sum/count/mean/min/max with explicit null handling.")
def table_aggregate(rows,group_by,operation,column=None,null_policy="error"):
    if operation not in {"sum","count","mean","min","max"}:raise ValueError("Unknown aggregate")
    if null_policy not in {"error","exclude"}:raise ValueError("Unknown null policy")
    if operation!="count" and not column:raise ValueError("A numeric column is required")
    groups={}
    for row in rows:
        key=tuple(row[c] for c in group_by)
        values=groups.setdefault(key,[])
        if operation=="count":values.append(Decimal(1));continue
        v=row[column]
        if v is None:
            if null_policy=="exclude":continue
            raise ValueError("Missing numeric value")
        values.append(number(v))
    if not rows and not group_by:groups[()]=[]
    out=[]
    for key,values in groups.items():
        if not values and operation not in {"count","sum"}:raise ValueError("Undefined aggregate on empty group")
        value={"sum":lambda:sum(values,Decimal(0)),"count":lambda:len(values),
          "mean":lambda:sum(values)/len(values),"min":lambda:min(values),"max":lambda:max(values)}[operation]()
        if operation in group_by:raise ValueError("Result column collision")
        out.append({**dict(zip(group_by,key)),operation:plain(value)})
    return out
@tool("table.join","Inner/left join with explicit keys, null-key and cardinality checks.")
def table_join(left,right,keys,how="inner",cardinality="many_to_one"):
    if how not in {"inner","left"} or cardinality not in {"one_to_one","many_to_one"}:raise ValueError("Unsupported join policy")
    if not keys:raise ValueError("Join keys required")
    def key(row):
        v=tuple(row[k] for k in keys)
        if any(x is None for x in v):raise ValueError("Null join key")
        return v
    index={}
    for r in right:
        k=key(r)
        if k in index:raise ValueError("Right join keys are not unique")
        index[k]=r
    if cardinality=="one_to_one" and len({key(r) for r in left})!=len(left):raise ValueError("Left join keys are not unique")
    rightcols=set().union(*(set(r)-set(keys) for r in right)) if right else set()
    out=[]
    for l in left:
        match=index.get(key(l))
        if match is None and how=="inner":continue
        nr=dict(l)
        for c in sorted(rightcols):
            dst=c+"_right" if c in nr else c
            if dst in nr:raise ValueError("Join output column collision")
            nr[dst]=match.get(c) if match else None
        out.append(nr)
    return out
OPS={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,ast.Mod:operator.mod}
def expression(formula,variables):
    if len(formula)>1000:raise ValueError("Formula too long")
    tree=ast.parse(formula,mode="eval")
    if sum(1 for _ in ast.walk(tree))>100:raise ValueError("Formula too complex")
    def visit(n):
        if isinstance(n,ast.Constant) and isinstance(n.value,(int,float)) and not isinstance(n.value,bool):return number(n.value)
        if isinstance(n,ast.Name):return number(variables[n.id])
        if isinstance(n,ast.BinOp) and type(n.op) in OPS:return OPS[type(n.op)](visit(n.left),visit(n.right))
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub)):return visit(n.operand)*(1 if isinstance(n.op,ast.UAdd) else -1)
        raise ValueError("Only numeric constants, named variables and arithmetic operators are allowed")
    return plain(visit(tree.body))
@tool("math.calculate","Evaluate restricted scalar arithmetic; never eval arbitrary code.")
def math_calculate(formula,variables):return expression(formula,variables)
@tool("table.calculate","Calculate a new column using a restricted formula.")
def table_calculate(rows,output_column,formula,overwrite=False):
    out=[]
    for r in rows:
        if output_column in r and not overwrite:raise ValueError("Output column exists")
        out.append({**r,output_column:expression(formula,r)})
    return out
@tool("table.select_columns","Project explicit table columns.")
def table_select(rows,columns):return [{k:r[k] for k in columns} for r in rows]
@tool("text.replace","Literal text replacement; does not edit a Word/PDF file.")
def text_replace(text,find,replacement,count=-1):
    if not find:raise ValueError("Empty search text")
    return text.replace(find,replacement,count)
@tool("text.split","Split text using an explicit literal separator.")
def text_split(text,separator):
    if not separator:raise ValueError("Empty separator")
    return text.split(separator)
@tool("text.combine","Combine text values with an explicit separator.")
def text_combine(items,separator):return separator.join(items)
@tool("text.statistics","Count characters or whitespace-separated words with explicit policy.")
def text_statistics(text,unit,include_whitespace=True):
    if unit=="words":return len(text.split())
    if unit=="characters":return len(text if include_whitespace else "".join(text.split()))
    raise ValueError("Unknown text unit")
@tool("json.parse","Parse JSON without interpreting code.")
def json_parse(text):return json.loads(text)
@tool("json.lookup","Read a JSON object/list through explicit key/index path.")
def json_lookup(value,path):
    for key in path:value=value[key]
    return value
@tool("list.select","Select a zero-based list index; no interactive UI action.")
def list_select(items,index):
    if isinstance(index,bool) or not isinstance(index,int) or index<0:raise ValueError("Nonnegative integer index required")
    return items[index]
@tool("date.adjust","Add explicit seconds to an ISO timestamp with timezone.")
def date_adjust(timestamp,seconds):
    dt=datetime.datetime.fromisoformat(timestamp)
    if dt.tzinfo is None:raise ValueError("Timezone required")
    return (dt+datetime.timedelta(seconds=seconds)).isoformat()
@tool("date.format","Format an ISO date/time; no inferred locale/timezone.")
def date_format(timestamp,format):return datetime.datetime.fromisoformat(timestamp).strftime(format)
@tool("url.parse","Parse URL components; does not open a browser or make an HTTP call.")
def url_parse(url):
    p=urlsplit(url)
    return {"scheme":p.scheme,"host":p.hostname,"path":p.path,"query":dict(parse_qsl(p.query)),"fragment":p.fragment}
@tool("url.build","Build a URL from explicit components and query parameters.")
def url_build(scheme,host,path,query=None):
    if scheme not in {"https","http"} or not host:raise ValueError("HTTP(S) scheme and host required")
    return urlunsplit((scheme,host,path,urlencode(query or {}),""))
@tool("codec.base64","Encode/decode UTF-8 text with validated Base64.")
def base64_text(text,mode):
    if mode=="encode":return base64.b64encode(text.encode()).decode()
    if mode=="decode":return base64.b64decode(text,validate=True).decode()
    raise ValueError("Unknown mode")
@tool("text.hash","Hash UTF-8 text with SHA256.")
def text_hash(text):return hashlib.sha256(text.encode()).hexdigest()
@dataclass
class Context:
    root:Path
    connectors:dict=field(default_factory=dict)
    def file(self,relative,*,must_be_file=False):
        root=self.root.resolve();p=(root/relative).resolve()
        if not p.is_relative_to(root):raise ValueError("File path leaves authorized root")
        if must_be_file and p.is_dir():raise IsADirectoryError("Expected a file, received a directory")
        return p
@tool("file.read_text","Read UTF-8 text within the selected workspace root.")
def read_text(path,context):return context.file(path,must_be_file=True).read_text("utf-8-sig")
@tool("file.read_csv","Read CSV rows as strings; numeric conversion must be explicit.")
def read_csv(path,context):
    with context.file(path,must_be_file=True).open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
@tool("file.write_json","Write JSON within the selected root; preserve existing files by default.")
def write_json(path,value,context,overwrite=False):
    p=context.file(path);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("w" if overwrite else "x",encoding="utf-8") as f:json.dump(value,f,ensure_ascii=False,indent=2)
    return {"path":p.relative_to(context.root.resolve()).as_posix()}
@tool("file.write_csv","Write CSV within the selected root with explicit column order.")
def write_csv(path,rows,columns,context,overwrite=False):
    p=context.file(path);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("w" if overwrite else "x",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=columns,extrasaction="raise");w.writeheader();w.writerows(rows)
    return {"path":p.relative_to(context.root.resolve()).as_posix()}
class MissingIntegration(RuntimeError):pass
@tool("connector.invoke","Call an explicitly configured connector; missing providers cannot execute.")
def invoke_connector(connector_id,operation,arguments,context):
    provider=context.connectors.get(connector_id)
    if provider is None:raise MissingIntegration("Connector is not configured: "+connector_id)
    return provider(operation,arguments)
def invoke(tool_id,args,context=None):
    entry=TOOLS.get(tool_id)
    if not entry:raise ValueError("Unknown tool: "+tool_id)
    fn=entry["function"];kwargs=dict(args)
    if "context" in inspect.signature(fn).parameters:
        if context is None:raise ValueError("Execution context required")
        kwargs["context"]=context
    inspect.signature(fn).bind(**kwargs)
    return fn(**kwargs)
def run(workflow,inputs,context=None):
    nodes=workflow["nodes"];byid={n["id"]:n for n in nodes}
    if len(byid)!=len(nodes):raise ValueError("Duplicate workflow node IDs")
    def references(value):
        if isinstance(value,dict):
            if set(value)=={"$ref"}:return {value["$ref"]}
            return set().union(*(references(v) for v in value.values())) if value else set()
        if isinstance(value,list):return set().union(*(references(v) for v in value)) if value else set()
        return set()
    deps={n["id"]:references(n.get("args",{}))|set(n.get("depends_on",[])) for n in nodes}
    if any(d not in byid for ds in deps.values() for d in ds):raise ValueError("Unknown dependency")
    # Validate tool names and argument names before any node executes.
    for n in nodes:
        if n["tool"] not in TOOLS:raise ValueError("Unknown tool")
        sig=inspect.signature(TOOLS[n["tool"]]["function"]);args=dict(n["args"])
        if "context" in sig.parameters:args["context"]=context
        sig.bind(**args)
    order=[];pending=set(byid)
    while pending:
        ready=sorted(i for i in pending if deps[i]<=set(order))
        if not ready:raise ValueError("Workflow contains a cycle")
        order.extend(ready);pending.difference_update(ready)
    outputs={}
    def resolve(v):
        if isinstance(v,dict):
            if set(v)=={"$ref"}:return outputs[v["$ref"]]
            if set(v)=={"$input"}:return inputs[v["$input"]]
            return {k:resolve(x) for k,x in v.items()}
        if isinstance(v,list):return [resolve(x) for x in v]
        return v
    for i in order:outputs[i]=invoke(byid[i]["tool"],resolve(byid[i]["args"]),context)
    return {"outputs":outputs,"order":order}
def catalog():
    result=[]
    for id,e in TOOLS.items():
        params=[]
        for name,p in inspect.signature(e["function"]).parameters.items():
            if name=="context":continue
            d={"name":name,"required":p.default is inspect.Parameter.empty}
            if p.default is not inspect.Parameter.empty:d["default"]=p.default
            params.append(d)
        result.append({"id":id,"description":e["description"],"parameters":params,
          "implementation":"runtime.py:invoke","status":"provider_required" if id=="connector.invoke" or e.get("provider_required") else "implemented_prototype",
          "provider":e.get("provider"),"side_effect":e.get("side_effect"),
          "backend_integrated":False,"version":"expansion-v1"})
    return result

@tool("list.count","Count list items; this is not word counting.")
def list_count(items):return len(items)
@tool("table.cast","Explicitly convert named CSV columns to numeric values.")
def table_cast(rows,columns):
    return [{k:(plain(number(v)) if k in columns else v) for k,v in r.items()} for r in rows]

from .google_tools import register_google_tools
register_google_tools(tool,invoke_connector,TOOLS)

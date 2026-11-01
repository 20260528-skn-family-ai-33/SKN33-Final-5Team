"""Canonical operations on explicit assets, logical documents and record tables.
Writes are copy-on-write under Context.root; original assets are preserved.
"""
import copy,csv,datetime as dt,difflib,hashlib,json,math,re,uuid
from zoneinfo import ZoneInfo
from workflow_engine.unified_tools.parser_errors import parse_file
OPERATIONS={}
def operation(tool,name,scope,dependencies=(),writes=False):
 def register(fn):
  OPERATIONS[(tool,name)]={"function":fn,"scope":scope,"dependencies":list(dependencies),"side_effect":writes}
  return fn
 return register
def text(d):
 if isinstance(d,str):return d
 if isinstance(d,dict) and isinstance(d.get("text"),str):return d["text"]
 if isinstance(d,dict) and isinstance(d.get("pages"),list):return "\n".join(p["text"] for p in d["pages"])
 raise ValueError("Document.text or pages[].text required")
def document(value,**metadata):return {"text":value,"version":hashlib.sha256(value.encode()).hexdigest(),**metadata}
def rows(t):
 r=t["rows"] if isinstance(t,dict) else t
 if not isinstance(r,list) or any(not isinstance(x,dict) for x in r):raise ValueError("Table.rows must contain record objects")
 return copy.deepcopy(r)
def table(value,columns=None):return {"columns":columns if columns is not None else list(dict.fromkeys(k for r in value for k in r)),"rows":value}
def transformed_table(source,result):
 original=rows(source)
 columns=copy.deepcopy(source["columns"]) if isinstance(source,dict) and "columns" in source else list(dict.fromkeys(k for r in original for k in r))
 if not isinstance(columns,list):raise ValueError("Table.columns must be a list")
 for r in result:
  for key in r:
   if key not in columns:columns.append(key)
 return table(result,columns)

def path(asset,context):
 if context is None:raise ValueError("Context required")
 return context.file(asset if isinstance(asset,str) else asset["path"],must_be_file=True)
def version(asset,expected,context):
 p=path(asset,context)
 if expected!=hashlib.sha256(p.read_bytes()).hexdigest():raise ValueError("Asset version conflict")
 return p
def output(context,suffix):
 p=context.file("generated/"+uuid.uuid4().hex+suffix);p.parent.mkdir(parents=True,exist_ok=True);return p
def artifact(p,context,**extra):return {"path":p.relative_to(context.root.resolve()).as_posix(),"version":hashlib.sha256(p.read_bytes()).hexdigest(),"bytes":p.stat().st_size,**extra}
def enum(value,allowed):
 if value not in allowed:raise ValueError("Unsupported policy: "+str(value))
 return value
def positive(value):
 if type(value)!=int or value<1:raise ValueError("Positive integer required")
 return value
def numbered(reader,numbers):
 ns=list(range(1,len(reader.pages)+1)) if numbers is None else numbers
 if not ns or any(type(n)!=int or not 1<=n<=len(reader.pages) for n in ns):raise ValueError("One-based page numbers out of range")
 return ns

@operation("read_document","extract_content","UTF-8 TXT/MD/HTML, DOCX, text-layer PDF only; no HWP, legacy DOC or OCR",["python-docx","pypdf"])
def extract_content(asset,format,context,profile_id=None,page_numbers=None):
 p=path(asset,context);fmt=format.lower()
 if profile_id is not None:raise ValueError("Named profiles need a parser adapter")
 if fmt=="pdf":
  from pypdf import PdfReader
  r=parse_file(PdfReader,"pdf",p);pages=[{"page":n,"text":r.pages[n-1].extract_text() or ""} for n in numbered(r,page_numbers)]
  return {"document":document("\n".join(x["text"] for x in pages),pages=pages,source_id=str(asset),ocr_performed=False)}
 if page_numbers is not None:raise ValueError("Page selection is PDF-only")
 if fmt in {"txt","md","text","markdown"}:value=p.read_text("utf-8-sig")
 elif fmt=="docx":
  from docx import Document
  from docx.text.paragraph import Paragraph
  d=parse_file(Document,"docx",p);parts=[]
  # Preserve body block order rather than appending every table after the paragraphs.
  for block in d.iter_inner_content():
   if isinstance(block,Paragraph):parts.append(block.text)
   else:parts.extend("\t".join(c.text for c in row.cells) for row in block.rows)
  value="\n".join(parts)
 elif fmt in {"html","htm"}:
  from workflow_engine.unified_tools.file_operations import parse_html
  value=parse_html(p.read_text("utf-8-sig"))["document"]["text"]
 else:raise ValueError("Unsupported format; HWP/OCR requires a separate parser")
 return {"document":document(value,source_id=str(asset),extraction_method=fmt)}

@operation("read_table","parse","CSV/TSV, JSON records, XLSX; unique text headers; XLSX formulas preserved as text",["openpyxl"])
def parse_table(source,format,context,profile_id=None,sheet_names=None,header_row=1,table_ids=None):
 if profile_id is not None or table_ids is not None:raise ValueError("Named profiles/table IDs require another adapter")
 positive(header_row);p=path(source,context);fmt=format.lower();result=[]
 def matrix(m,name):
  if len(m)<header_row:raise ValueError("Header row out of range")
  cols=list(m[header_row-1])
  if any(not isinstance(c,str) or not c for c in cols) or len(set(cols))!=len(cols):raise ValueError("Headers must be unique nonempty strings")
  data=[]
  for r in m[header_row:]:
   if len(r)>len(cols):raise ValueError("Row exceeds header width")
   data.append(dict(zip(cols,list(r)+[None]*(len(cols)-len(r)))))
  result.append({**table(data,cols),"name":name})
 if fmt in {"csv","tsv"}:
  if sheet_names:raise ValueError("Sheet selection is XLSX-only")
  with p.open(encoding="utf-8-sig",newline="") as f:matrix(list(csv.reader(f,delimiter="\t" if fmt=="tsv" else ",")),p.stem)
 elif fmt=="json":
  if sheet_names or header_row!=1:raise ValueError("JSON has no sheets/header rows")
  result.append(table(rows(json.loads(p.read_text("utf-8-sig")))))
 elif fmt=="xlsx":
  from openpyxl import load_workbook
  wb=parse_file(load_workbook,"xlsx",p,read_only=True,data_only=False)
  try:
   for name in sheet_names or wb.sheetnames:matrix(list(wb[name].values),name)
  finally:wb.close()
 else:raise ValueError("Supported table formats: csv/tsv/json/xlsx")
 return {"tables":result}

@operation("search_document","retrieve_passages","Lexical token-overlap retrieval only; no semantic embeddings")
def retrieve_passages(documents,query,top_k):
 positive(top_k);terms=set(re.findall(r"\w+",query.casefold()));hits=[]
 if not terms:raise ValueError("Query is empty")
 for i,d in enumerate(documents):
  for j,part in enumerate(text(d).splitlines()):
   score=len(terms & set(re.findall(r"\w+",part.casefold())))
   if score:hits.append((score,i,j,{"text":part,"source_id":d.get("source_id",str(i)),"page":d.get("page"),"score":score,"method":"lexical_overlap"}))
 return {"passages":[h[3] for h in sorted(hits,key=lambda x:(-x[0],x[1],x[2]))[:top_k]]}

@operation("filter_sort","deduplicate","Exact equality on explicit columns; keep=first/last/none")
def deduplicate(table,columns,keep):
 enum(keep,{"first","last","none"});data=rows(table);groups={}
 if not columns:raise ValueError("Dedup columns required")
 for i,r in enumerate(data):groups.setdefault(json.dumps([r[c] for c in columns],sort_keys=True),[]).append(i)
 selected={ix[0] if keep=="first" else ix[-1] for ix in groups.values()} if keep!="none" else {ix[0] for ix in groups.values() if len(ix)==1}
 return {"table":transformed_table(table,[r for i,r in enumerate(data) if i in selected])}
@operation("filter_sort","handle_missing","Missing=null/absent key; action=drop_rows/fill/error/keep")
def handle_missing(table,columns,action,fill_value=None):
 enum(action,{"drop_rows","fill","error","keep"});result=[]
 for r in rows(table):
  missing=[c for c in columns if r.get(c) is None]
  if missing and action=="error":raise ValueError("Missing values: "+str(missing))
  if missing and action=="drop_rows":continue
  if action=="fill":
   for c in missing:r[c]=fill_value
  result.append(r)
 return {"table":transformed_table(table,result)}
@operation("aggregate_table","sum","Grouped finite numeric sum; null_policy=error/exclude")
def sum_table(table,group_by,value_column,null_policy):
 from workflow_engine.unified_tools.dispatcher import runtime
 return {"table":globals()["table"](runtime.table_aggregate(rows(table),group_by,"sum",value_column,null_policy))}
@operation("aggregate_table","count","Grouped rows/non_null/distinct counting")
def count_table(table,group_by,mode,column=None):
 enum(mode,{"rows","non_null","distinct"});groups={};out=[]
 if mode!="rows" and not column:raise ValueError("Count column required")
 if "count" in group_by:raise ValueError("Count column collision")
 for r in rows(table):groups.setdefault(tuple(r[c] for c in group_by),[]).append(r)
 if not groups and not group_by:groups[()]=[]
 for k,rs in groups.items():
  vals=[r[column] for r in rs if r.get(column) is not None] if column else []
  n=len(rs) if mode=="rows" else len(vals) if mode=="non_null" else len({json.dumps(v,sort_keys=True) for v in vals})
  out.append({**dict(zip(group_by,k)),"count":n})
 return {"table":globals()["table"](out)}
@operation("calculate_columns","compute","Restricted numeric arithmetic formula; no arbitrary eval")
def compute(table,output_column,formula):
 from workflow_engine.unified_tools.dispatcher import runtime
 return {"table":globals()["table"](runtime.table_calculate(rows(table),output_column,formula))}
@operation("calculate_columns","rename_columns","Simultaneous rename with unknown-key/collision checks")
def rename_columns(table,mapping):
 data=rows(table);cols=table.get("columns",[]) if isinstance(table,dict) else [];cols=cols or list(dict.fromkeys(c for r in data for c in r));dest=[mapping.get(c,c) for c in cols]
 if set(mapping)-set(cols) or any(not isinstance(c,str) or not c for c in dest) or len(set(dest))!=len(dest):raise ValueError("Invalid column rename/collision")
 return {"table":globals()["table"]([{mapping.get(c,c):v for c,v in r.items()} for r in data],dest)}
@operation("calculate_columns","parse_dates","Explicit strptime + IANA timezone; ambiguous/nonexistent local times rejected",["tzdata"])
def parse_dates(table,columns,input_format,timezone):
 zone=ZoneInfo(timezone);data=rows(table)
 for r in data:
  for c in columns:
   parsed=dt.datetime.strptime(r[c],input_format)
   if parsed.tzinfo:parsed=parsed.astimezone(zone)
   else:
    a=parsed.replace(tzinfo=zone,fold=0);b=parsed.replace(tzinfo=zone,fold=1)
    if a.utcoffset()!=b.utcoffset() or a.astimezone(dt.timezone.utc).astimezone(zone).replace(tzinfo=None)!=parsed:raise ValueError("Ambiguous/nonexistent local time")
    parsed=a
   r[c]=parsed.isoformat()
 return {"table":globals()["table"](data)}
@operation("join_tables","join","inner/left equal-named keys; one_to_one/many_to_one; null_keys=error; max_rows cap")
def join(left,right,keys,how,cardinality,null_keys,max_rows):
 from workflow_engine.unified_tools.dispatcher import runtime
 positive(max_rows);enum(null_keys,{"error"})
 if any(not isinstance(k,str) for k in keys):raise ValueError("Equal-named string join keys required")
 data=runtime.table_join(rows(left),rows(right),keys,how,cardinality)
 if len(data)>max_rows:raise ValueError("Join exceeds max_rows")
 return {"table":table(data)}
@operation("compare_tables","diff","Unique non-null keys; added/deleted/changed cells; absolute numeric tolerance")
def diff_tables(baseline,candidate,keys,compare_columns,absolute_tolerance):
 if type(absolute_tolerance) not in (int,float) or not math.isfinite(absolute_tolerance) or absolute_tolerance<0:raise ValueError("Nonnegative finite tolerance required")
 if not keys:raise ValueError("Comparison keys required")
 def index(t):
  result={}
  for r in rows(t):
   k=tuple(r[c] for c in keys)
   if any(x is None for x in k) or k in result:raise ValueError("Unique non-null row keys required")
   result[k]=r
  return result
 a=index(baseline);b=index(candidate);changes=[]
 for k in a:
  if k not in b:continue
  for c in compare_columns:
   x=a[k][c];y=b[k][c];equal=abs(x-y)<=absolute_tolerance if type(x) in (int,float) and type(y) in (int,float) else x==y
   if not equal:changes.append({"key":list(k),"column":c,"before":x,"after":y})
 return {"differences":{"added":[b[k] for k in b if k not in a],"deleted":[a[k] for k in a if k not in b],"changed":changes}}

def edit_text(d,expected,old,new,occurrence):
 original=text(d)
 if expected!=hashlib.sha256(original.encode()).hexdigest():raise ValueError("Document version conflict")
 if not old or old not in original:raise ValueError("Nonempty matching text required")
 enum(occurrence,{"first","all"});value=original.replace(old,new,1 if occurrence=="first" else -1)
 return {"document":document(value),"changes":[{"before":old,"after":new,"count":1 if occurrence=="first" else original.count(old)}]}
@operation("edit_document","replace_text","Versioned logical text; literal first/all replacement")
def replace_text(document,expected_version,find,replacement,occurrence):return edit_text(document,expected_version,find,replacement,occurrence)
@operation("edit_document","insert_text","Versioned logical text; unique literal anchor; before/after")
def insert_text(document,expected_version,anchor,position,text):
 enum(position,{"before","after"})
 if not anchor or globals()["text"](document).count(anchor)!=1:raise ValueError("Unique nonempty anchor required")
 return edit_text(document,expected_version,anchor,text+anchor if position=="before" else anchor+text,"first")
@operation("edit_document","delete_text","Versioned logical text; first/all literal deletion")
def delete_text(document,expected_version,target,occurrence):return edit_text(document,expected_version,target,"",occurrence)
@operation("compare_documents","diff","Logical text/line diff; no layout/semantic judgment")
def diff_documents(baseline,candidate,mode):
 enum(mode,{"text","lines"});a=text(baseline);b=text(candidate);a=a if mode=="text" else a.splitlines();b=b if mode=="text" else b.splitlines()
 issues=[{"operation":tag,"baseline_range":[i,j],"candidate_range":[k,l],"before":a[i:j],"after":b[k:l]} for tag,i,j,k,l in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes() if tag!="equal"]
 return {"review":{"issues":issues,"equal":not issues,"mode":mode}}
@operation("verify_content_requirements","check_required","Literal contains/not_contains conditions; semantic conditions rejected")
def check_required(document,requirements):
 value=text(document);checks=[]
 for r in requirements:
  kind=enum(r["type"],{"contains","not_contains"});needle=r["text"]
  if not needle:raise ValueError("Empty requirement")
  present=needle in value if r.get("case_sensitive",True) else needle.casefold() in value.casefold()
  checks.append({"id":r["id"],"passed":present if kind=="contains" else not present})
 return {"review":{"checks":checks,"passed":all(c["passed"] for c in checks)}}
@operation("verify_content_requirements","check_expected","Exact unique extracted field/value comparisons; no coercion")
def check_expected(extracted,expected):
 vals={}
 for v in extracted:
  if v["field"] in vals:raise ValueError("Duplicate extracted field")
  vals[v["field"]]=v["value"]
 checks=[{"field":v["field"],"expected":v["value"],"actual":vals.get(v["field"]),"passed":v["field"] in vals and vals[v["field"]]==v["value"]} for v in expected]
 return {"review":{"checks":checks,"passed":all(c["passed"] for c in checks)}}
@operation("review_summary","tabulate_reviews","Flatten explicit review issues and deduplicate non-null keys")
def tabulate_reviews(reviews,columns,dedup_key):
 if dedup_key not in columns:raise ValueError("Dedup key must be an output column")
 out=[];seen=set()
 for r in reviews:
  for issue in r["issues"]:
   k=issue[dedup_key]
   if k is None:raise ValueError("Null dedup key")
   marker=json.dumps(k,sort_keys=True)
   if marker not in seen:out.append({c:issue.get(c) for c in columns});seen.add(marker)
 return {"table":table(out,columns)}
def links(d):
 if isinstance(d,dict) and "links" in d:return d["links"]
 return list(dict.fromkeys(re.findall(r"\[[^\]]*\]\(([^\s)]+)\)",text(d))))
@operation("check_document_links","check_local_targets","Explicit/Markdown links against local assets; URL fragments not checked")
def check_local_targets(document,local_assets,context):
 from urllib.parse import urlsplit,unquote
 allowed={str(path(a,context).resolve()) for a in local_assets};checks=[]
 for target in links(document):
  u=urlsplit(target)
  if u.scheme or u.netloc:continue
  p=path(unquote(u.path),context) if u.path else None
  checks.append({"target":target,"passed":p is not None and str(p.resolve()) in allowed and p.is_file(),"fragment_checked":False})
 return {"review":{"checks":checks,"passed":all(c["passed"] for c in checks),"scope":"local files only"}}
@operation("text_statistics","measure","Unicode code points, whitespace words, splitlines; explicit whitespace policy")
def measure(document,units,whitespace,word_policy):
 enum(whitespace,{"include","exclude"});enum(word_policy,{"whitespace"});value=text(document);result={}
 for u in units:
  enum(u,{"characters","words","lines"});result[u]=len(value if whitespace=="include" else "".join(value.split())) if u=="characters" else len(value.split()) if u=="words" else len(value.splitlines())
 return {"counts":result}
@operation("verify_table_calculations","check_totals","Explicit sum/count/mean/min/max versus expected; finite absolute tolerance")
def check_totals(table,checks,absolute_tolerance):
 from workflow_engine.unified_tools.dispatcher import runtime
 if type(absolute_tolerance) not in (int,float) or not math.isfinite(absolute_tolerance) or absolute_tolerance<0:raise ValueError("Nonnegative finite tolerance required")
 result=[]
 for c in checks:
  op=c["operation"];value=runtime.table_aggregate(rows(table),[],op,c.get("column"),c.get("null_policy","error"))[0][op];expected=runtime.plain(runtime.number(c["expected"]))
  result.append({"id":c["id"],"actual":value,"expected":expected,"passed":abs(value-expected)<=absolute_tolerance})
 return {"review":{"checks":result,"passed":all(c["passed"] for c in result)}}

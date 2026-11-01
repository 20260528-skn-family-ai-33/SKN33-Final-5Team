"""Optional file-format operations; creates new files and preserves inputs."""
import json,math,re,shutil,subprocess,os
from pathlib import Path
from html.parser import HTMLParser
from .parser_errors import parse_file
from .local_operations import operation,document,text,rows,table,path,version,output,artifact,enum,positive,numbered,OPERATIONS
class Html(HTMLParser):
 def __init__(self):
  super().__init__(convert_charrefs=True);self.parts=[];self.tables=[];self.current=None;self.row=None;self.cell=None;self.skip=0
 def handle_starttag(self,tag,attrs):
  if tag in {"script","style"}:self.skip+=1
  if tag=="table":
   if self.current is not None:raise ValueError("Nested tables require a parser adapter")
   self.current=[]
  if tag=="tr" and self.current is not None:self.row=[]
  if tag in {"td","th"} and self.row is not None:
   if any(k in {"rowspan","colspan"} and v!="1" for k,v in attrs):raise ValueError("Merged HTML cells need an explicit flattening policy")
   self.cell=[]
 def handle_endtag(self,tag):
  if tag in {"script","style"}:self.skip=max(0,self.skip-1)
  if tag in {"td","th"} and self.cell is not None:self.row.append(" ".join(self.cell).strip());self.cell=None
  if tag=="tr" and self.row is not None:self.current.append(self.row);self.row=None
  if tag=="table" and self.current is not None:self.tables.append(self.current);self.current=None
 def handle_data(self,data):
  if not self.skip and data.strip():self.parts.append(data.strip())
  if self.cell is not None and not self.skip:self.cell.append(data)
def parse_html(value):
 h=Html();h.feed(value);tables=[]
 for m in h.tables:
  if not m:continue
  cols=m[0]
  if len(set(cols))!=len(cols) or any(not c for c in cols) or any(len(r)!=len(cols) for r in m[1:]):raise ValueError("HTML table needs unique headers and rectangular rows")
  tables.append(table([dict(zip(cols,r)) for r in m[1:]],cols))
 return {"document":document("\n".join(h.parts)),"tables":tables}
@operation("read_web","extract_html","Local HTML file; text/tables/both; no implicit network or CSS selectors")
def extract_html(asset,extract,context,selector=None):
 if selector is not None:raise ValueError("CSS selectors require another adapter")
 enum(extract,{"text","tables","both"});result=parse_html(path(asset,context).read_text("utf-8-sig"))
 return {"document":result["document"] if extract!="tables" else document(""),"tables":result["tables"] if extract!="text" else []}
def literal_cell(cell,value):
 if isinstance(value,(dict,list)):raise ValueError("Spreadsheet cells must be scalar")
 if type(value)==float and not math.isfinite(value):raise ValueError("Non-finite spreadsheet number")
 cell.value=value
 if isinstance(value,str):cell.data_type="s"  # Untrusted '=...' remains literal text.
def sheet(wb,spec):
 name=spec["name"]
 if name in wb.sheetnames:raise ValueError("Duplicate sheet name")
 ws=wb.create_sheet(name);t=spec["table"];data=rows(t);cols=t.get("columns") or list(dict.fromkeys(c for r in data for c in r))
 if len(set(cols))!=len(cols) or any(not isinstance(c,str) or not c for c in cols):raise ValueError("Unique nonempty sheet headers required")
 for i,r in enumerate([dict(zip(cols,cols))]+data,1):
  if set(r)-set(cols):raise ValueError("Row contains unknown columns")
  for j,c in enumerate(cols,1):literal_cell(ws.cell(i,j),r.get(c))
 return ws
@operation("create_spreadsheet","create","XLSX named sheets of literal scalar cells; formulas not evaluated",["openpyxl"],True)
def create_spreadsheet(sheets,context):
 from openpyxl import Workbook
 if not sheets:raise ValueError("At least one sheet required")
 wb=Workbook();wb.remove(wb.active)
 for s in sheets:sheet(wb,s)
 p=output(context,".xlsx");wb.save(p);wb.close();return {"artifact":artifact(p,context)}
def workbook_edit(workbook,expected_version,context,edit):
 from openpyxl import load_workbook
 source=version(workbook,expected_version,context)
 if source.suffix.lower()!=".xlsx":raise ValueError("XLSX required")
 wb=parse_file(load_workbook,"xlsx",source)
 try:changes=edit(wb);p=output(context,".xlsx");wb.save(p)
 finally:wb.close()
 return {"artifact":artifact(p,context),"changes":changes}
@operation("edit_spreadsheet","add_sheet","Copy-on-write XLSX with SHA-256 version check",["openpyxl"],True)
def add_sheet(workbook,expected_version,sheet,context):
 def edit(wb):globals()["sheet"](wb,sheet);return [{"action":"add_sheet","sheet":sheet["name"]}]
 return workbook_edit(workbook,expected_version,context,edit)
@operation("edit_spreadsheet","append_rows","Append to existing XLSX headers; rejects unknown columns",["openpyxl"],True)
def append_rows(workbook,expected_version,sheet_name,table,context):
 def edit(wb):
  ws=wb[sheet_name];cols=[c.value for c in ws[1]]
  if any(not isinstance(c,str) for c in cols) or len(set(cols))!=len(cols):raise ValueError("Invalid sheet headers")
  data=rows(table)
  for r in data:
   if set(r)-set(cols):raise ValueError("Unknown append column")
   n=ws.max_row+1
   for i,c in enumerate(cols,1):literal_cell(ws.cell(n,i),r.get(c))
  return [{"action":"append_rows","count":len(data)}]
 return workbook_edit(workbook,expected_version,context,edit)
@operation("edit_spreadsheet","set_cells","CellEdit={sheet_name,cell,value}; literal scalars",["openpyxl"],True)
def set_cells(workbook,expected_version,cells,context):
 def edit(wb):
  for c in cells:literal_cell(wb[c["sheet_name"]][c["cell"]],c["value"])
  return [{"action":"set_cell","sheet":c["sheet_name"],"cell":c["cell"]} for c in cells]
 return workbook_edit(workbook,expected_version,context,edit)
@operation("edit_spreadsheet","fill_sequence","Row-major numeric sequence; at most 100000 cells",["openpyxl"],True)
def fill_sequence(workbook,expected_version,sheet_name,cell_range,start,step,context):
 from openpyxl.utils.cell import range_boundaries
 if type(start) not in (int,float) or type(step) not in (int,float) or not math.isfinite(start) or not math.isfinite(step):raise ValueError("Finite start/step required")
 a,b,c,d=range_boundaries(cell_range)
 if None in (a,b,c,d) or c<a or d<b or (c-a+1)*(d-b+1)>100000:raise ValueError("Explicit bounded cell range required")
 def edit(wb):
  ws=wb[sheet_name];n=0
  for row in ws.iter_rows(min_row=b,max_row=d,min_col=a,max_col=c):
   for cell in row:
    value=start+n*step
    if not math.isfinite(value):raise ValueError("Sequence overflow")
    literal_cell(cell,value);n+=1
  return [{"action":"fill_sequence","count":n}]
 return workbook_edit(workbook,expected_version,context,edit)
@operation("create_document","compose","TXT/MD/DOCX heading+text sections; no content generation",["python-docx"],True)
def compose(sections,format,context):
 fmt=enum(format.lower(),{"txt","md","docx"});value="\n\n".join((s.get("heading","")+"\n"+s["text"]).strip() for s in sections);p=output(context,"."+fmt)
 if fmt=="docx":
  from docx import Document
  d=Document()
  for s in sections:
   if s.get("heading"):d.add_heading(s["heading"],level=1)
   d.add_paragraph(s["text"])
  d.save(p)
 else:p.write_text(value,encoding="utf-8")
 return {"artifact":artifact(p,context)}
@operation("format_document","apply_formatting","DOCX paragraph font/style/alignment; no table/header or page-layout formatting",["python-docx"],True)
def apply_formatting(document,expected_version,rules,context):
 from docx import Document
 from docx.shared import Pt
 from docx.enum.text import WD_ALIGN_PARAGRAPH as A
 source=version(document,expected_version,context)
 if source.suffix.lower()!=".docx":raise ValueError("DOCX required")
 d=parse_file(Document,"docx",source);changes=[]
 for rule in rules:
  if set(rule)-{"paragraph","bold","italic","font_size","font_name","style","alignment"}:raise ValueError("Unsupported formatting rule")
  n=positive(rule["paragraph"])
  if n>len(d.paragraphs):raise ValueError("Paragraph out of range")
  p=d.paragraphs[n-1]
  if "style" in rule:p.style=rule["style"]
  if "alignment" in rule:p.alignment={"left":A.LEFT,"center":A.CENTER,"right":A.RIGHT,"justify":A.JUSTIFY}[rule["alignment"]]
  for r in p.runs:
   if "bold" in rule:r.bold=rule["bold"]
   if "italic" in rule:r.italic=rule["italic"]
   if "font_size" in rule:
    if not 1<=rule["font_size"]<=200:raise ValueError("Font size out of range")
    r.font.size=Pt(rule["font_size"])
   if "font_name" in rule:r.font.name=rule["font_name"]
  changes.append({"paragraph":n,"rule":rule})
 p=output(context,".docx");d.save(p);return {"artifact":artifact(p,context),"changes":changes}
@operation("fill_template","fill_fields","TXT/MD {{identifier}} templates; missing_policy=error/keep/empty",(),True)
def fill_fields(template,field_values,missing_policy,context):
 enum(missing_policy,{"error","keep","empty"});p=path(template,context)
 if p.suffix.lower() not in {".txt",".md"}:raise ValueError("Text templates only; DOCX needs a run-aware filler")
 value=p.read_text("utf-8-sig");pattern=r"\{\{\s*([A-Za-z_][\w.]*)\s*\}\}";missing=sorted(set(re.findall(pattern,value))-set(field_values))
 if missing and missing_policy=="error":raise ValueError("Missing template fields: "+str(missing))
 if any(not isinstance(v,(str,int,float,bool)) and v is not None for v in field_values.values()):raise ValueError("Scalar template values required")
 result=re.sub(pattern,lambda m:str(field_values[m[1]]) if m[1] in field_values else m[0] if missing_policy=="keep" else "",value)
 p=output(context,p.suffix.lower());p.write_text(result,encoding="utf-8");return {"artifact":artifact(p,context),"missing_fields":missing}
@operation("convert_format","convert","TXT/MD/DOCX/PDF->text; TXT/MD->DOCX; CSV<->JSON; lossy conversion requires allow",["python-docx","pypdf"],True)
def convert(asset,source_format,target_format,loss_policy,context):
 import csv
 from .local_operations import extract_content,parse_table
 enum(loss_policy,{"error","allow"});sf=source_format.lower();tf=target_format.lower();lossy=sf in {"docx","pdf"} and tf in {"txt","md"}
 if lossy and loss_policy!="allow":raise ValueError("Formatting loss requires loss_policy=allow")
 if sf in {"txt","md","docx","pdf"} and tf in {"txt","md"}:
  value=extract_content(asset,sf,context)["document"]["text"];p=output(context,"."+tf);p.write_text(value,encoding="utf-8");a=artifact(p,context)
 elif sf in {"txt","md"} and tf=="docx":a=compose([{"text":path(asset,context).read_text("utf-8-sig")}],"docx",context)["artifact"]
 elif {sf,tf}=={"csv","json"}:
  t=parse_table(asset,sf,context)["tables"][0];p=output(context,"."+tf)
  if tf=="json":p.write_text(json.dumps(t["rows"],ensure_ascii=False,indent=2),encoding="utf-8")
  else:
   with p.open("w",encoding="utf-8-sig",newline="") as f:
    w=csv.DictWriter(f,fieldnames=t["columns"]);w.writeheader();w.writerows(t["rows"])
  a=artifact(p,context)
 else:raise ValueError("Unsupported conversion pair")
 return {"artifact":a,"loss_report":{"lossy":lossy,"issues":[{"type":"formatting_removed"}] if lossy else []}}
@operation("validate_document_format","report_only","Compare DOCX paragraph font/name/bold/italic/style/alignment; no pixel judgment",["python-docx"])
def validate_format(asset,reference,checks,context):
 from docx import Document
 if any(path(a,context).suffix.lower()!=".docx" for a in [asset,reference]):raise ValueError("DOCX required")
 a=parse_file(Document,"docx",path(asset,context));b=parse_file(Document,"docx",path(reference,context));result=[]
 for c in checks:
  n=positive(c["paragraph"]);prop=enum(c["property"],{"style","alignment","font_size","font_name","bold","italic"})
  if n>len(a.paragraphs) or n>len(b.paragraphs):result.append({"paragraph":n,"property":prop,"passed":False,"reason":"missing_paragraph"});continue
  def val(p):
   if prop=="style":return p.style.name
   if prop=="alignment":return p.alignment
   return [getattr(r.font,{"font_size":"size","font_name":"name"}.get(prop,prop)) for r in p.runs]
  result.append({"paragraph":n,"property":prop,"passed":val(a.paragraphs[n-1])==val(b.paragraphs[n-1])})
 return {"review":{"checks":result,"passed":all(x["passed"] for x in result)}}
def pdf_extract(asset,page_numbers,context):
 from pypdf import PdfReader,PdfWriter
 r=parse_file(PdfReader,"pdf",path(asset,context));ns=numbered(r,page_numbers);w=PdfWriter()
 for n in ns:w.add_page(r.pages[n-1])
 p=output(context,".pdf")
 with p.open("wb") as f:w.write(f)
 return artifact(p,context,page_numbers=ns)
@operation("split_pdf","extract_pages","One-based PDF extraction, preserves specified order",["pypdf"],True)
def extract_pages(asset,page_numbers,context):return {"artifact":pdf_extract(asset,page_numbers,context)}
@operation("split_pdf","split","PageGroup={page_numbers:[one-based integers]}; new files",["pypdf"],True)
def split_pdf(asset,groups,context):
 from pypdf import PdfReader
 r=parse_file(PdfReader,"pdf",path(asset,context))
 if not groups:raise ValueError("Page groups required")
 for g in groups:numbered(r,g["page_numbers"])
 return {"artifacts":[pdf_extract(asset,g["page_numbers"],context) for g in groups]}
@operation("merge_documents","merge","PDF concatenation, UTF-8 TXT/MD concatenation; no mixed formats",["pypdf"],True)
def merge(assets,format,context):
 if not assets:raise ValueError("Assets required")
 fmt=enum(format.lower(),{"pdf","txt","md"});p=output(context,"."+fmt)
 if fmt=="pdf":
  from pypdf import PdfReader,PdfWriter
  readers=[parse_file(PdfReader,"pdf",path(a,context)) for a in assets];w=PdfWriter()
  for r in readers:
   for page in r.pages:w.add_page(page)
  with p.open("wb") as f:w.write(f)
 else:p.write_text("\n".join(path(a,context).read_text("utf-8-sig") for a in assets),encoding="utf-8")
 return {"artifact":artifact(p,context)}
@operation("compress_pdf","optimize","Lossless PDF stream compression; target size measured, not guaranteed",["pypdf"],True)
def optimize(asset,target_bytes,quality_policy,context):
 from pypdf import PdfReader,PdfWriter
 positive(target_bytes);enum(quality_policy,{"lossless"});source=path(asset,context);w=PdfWriter();w.clone_document_from_reader(parse_file(PdfReader,"pdf",source))
 for page in w.pages:page.compress_content_streams()
 p=output(context,".pdf")
 with p.open("wb") as f:w.write(f)
 if p.stat().st_size>source.stat().st_size:shutil.copyfile(source,p)
 return {"artifact":artifact(p,context),"target_met":p.stat().st_size<=target_bytes,"loss_report":{"lossy":False,"before_bytes":source.stat().st_size,"after_bytes":p.stat().st_size}}
@operation("render_document_pages","render","PDF->PNG via A4B_PDFTOPPM/PATH; pages one-based, dpi 36..300, max dimension 6000",["pypdf","pdftoppm"],True)
def render(asset,page_numbers,dpi,context):
 from pypdf import PdfReader
 positive(dpi)
 if not 36<=dpi<=300:raise ValueError("DPI must be 36..300")
 executable=os.getenv("A4B_PDFTOPPM") or shutil.which("pdftoppm")
 if not executable or not Path(executable).is_file():raise RuntimeError("Configure A4B_PDFTOPPM")
 p=path(asset,context)
 if p.suffix.lower()!=".pdf":raise ValueError("Rendering supports PDF only")
 r=parse_file(PdfReader,"pdf",p);ns=numbered(r,page_numbers)
 if len(ns)>100:raise ValueError("At most 100 pages per call")
 result=[]
 for n in ns:
  target=output(context,".png")
  args=[str(executable),"-f",str(n),"-l",str(n),"-singlefile","-r",str(dpi),"-png"]
  box=r.pages[n-1].mediabox
  if max(float(box.width),float(box.height))*dpi/72>6000:args+=["-scale-to","6000"]
  subprocess.run(args+[str(p),str(target.with_suffix(""))],check=True,capture_output=True,timeout=60)
  result.append({**artifact(target,context),"page":n,"requested_dpi":dpi,"max_dimension":6000})
 return {"pages":result}
@operation("annotate_document","add_annotations","PDF text notes/highlights with point rectangles; flatten=False only",["pypdf"],True)
def add_annotations(asset,annotations,flatten,context):
 from pypdf import PdfReader,PdfWriter
 from pypdf.annotations import Text,Highlight
 from pypdf.generic import ArrayObject,FloatObject
 if flatten:raise ValueError("Flattening unsupported; annotations remain interactive")
 r=parse_file(PdfReader,"pdf",path(asset,context));w=PdfWriter();w.clone_document_from_reader(r);changes=[]
 for a in annotations:
  n=a["page"];numbered(r,[n]);rect=a["rect"]
  if len(rect)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in rect) or rect[2]<=rect[0] or rect[3]<=rect[1]:raise ValueError("Invalid annotation rectangle")
  box=r.pages[n-1].mediabox
  if rect[0]<float(box.left) or rect[1]<float(box.bottom) or rect[2]>float(box.right) or rect[3]>float(box.top):raise ValueError("Annotation outside page")
  kind=enum(a["type"],{"note","highlight"})
  if kind=="note":ann=Text(rect=rect,text=a["text"])
  else:
   x0,y0,x1,y1=rect;ann=Highlight(rect=rect,quad_points=ArrayObject([FloatObject(v) for v in [x0,y1,x1,y1,x0,y0,x1,y0]]))
  w.add_annotation(n-1,ann);changes.append({"page":n,"type":kind})
 p=output(context,".pdf")
 with p.open("wb") as f:w.write(f)
 return {"artifact":artifact(p,context),"changes":changes}

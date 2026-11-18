import {useState} from 'react';

type Cell = string | number | boolean | null;
type InspectedTable = {name:string; columns:string[]; preview:Record<string,Cell>[]; block_id?:string};
export type InspectedFile = {
  id:string; name:string; kind:string; has_readable_text:boolean; partial?:boolean;
  columns:string[]; fields:{label:string; value?:Cell}[]; snippets:string[]; preview:Record<string,Cell>[];
  tables?:InspectedTable[];
};

export function PreviewRows({label,columns,rows}:{label:string;columns:string[];rows:Record<string,Cell>[]}) {
  if (!rows.length || !columns.length) return null;
  return <div className="as-table-scroll as-file-preview"><table>
    <caption>{label}</caption>
    <thead><tr>{columns.map(column=><th key={column} scope="col">{column}</th>)}</tr></thead>
    <tbody>{rows.slice(0,3).map((row,index)=><tr key={index}>{columns.map(column=>
      <td key={column}>{row[column] == null || row[column] === '' ? <span className="as-small-muted">확인 안 됨</span> : String(row[column])}</td>
    )}</tr>)}</tbody>
  </table></div>;
}

type FileDetails = {label:string;open:()=>void};
type DocumentTableSelection = {disabled:boolean;onAnswer:(message:string,selection?:{file_id:string;block_id:string;fields:string[]})=>void};

function DocumentTableColumns({fileId,fileName,table,selection}:{
  fileId:string;fileName:string;table:InspectedTable;selection:DocumentTableSelection
}) {
  const [selected,setSelected]=useState<string[]>([]);
  const fields=table.columns.filter(column=>selected.includes(column));
  return <fieldset className="as-document-columns" disabled={selection.disabled}>
    <legend>{table.name}에서 추출할 열</legend>
    <div className="as-document-column-options">{table.columns.map(column=><label key={column}>
      <input type="checkbox" checked={fields.includes(column)} onChange={event=>{
        const checked=event.target.checked;
        setSelected(prior=>checked?[...prior,column]:prior.filter(value=>value!==column));
      }}/>{column}
    </label>)}</div>
    <button type="button" className="as-button" disabled={!fields.length} onClick={()=>{
      const aliases=fields.map(column=>'「'+table.name+'」의 '+column);
      selection.onAnswer(fileName+'에서 '+aliases.join(', ')+' 항목을 추출해 주세요.',table.block_id?{file_id:fileId,block_id:table.block_id,fields}:undefined);
    }}>선택한 열로 답변</button>
  </fieldset>;
}

function InspectedSource({file,open,details,selection}:{
  file:InspectedFile;open:boolean;details?:FileDetails;selection?:DocumentTableSelection
}) {
  const tables=file.tables || [];
  const tableSelection=file.kind==='document'?selection:undefined;
  const primaryIsNamed=tables.some(table=>file.columns.length===table.columns.length &&
    file.columns.every((column,i)=>column===table.columns[i]) && file.preview.length===table.preview.length &&
    file.preview.every((row,i)=>file.columns.every(column=>{
      const a=row[column],b=table.preview[i][column];
      return a===b || (a!=null && b!=null && String(a)===String(b));
    })));
  return <details open={open || (tableSelection && tables.some(table=>table.block_id && table.preview.length))} className="as-inspected-source">
    <summary>{file.name} · {file.kind==='table'?'표':'문서'} 미리보기</summary>
    {file.partial&&<p>일부 내용은 확인이 필요합니다. 원본과 대조해 주세요.</p>}
    {!file.has_readable_text && !file.columns.length&&!tables.length&&<p>읽어 낸 글자가 없습니다. 원본을 확인하거나 더 선명한 파일을 연결해 주세요.</p>}
    {file.snippets.length>0&&<blockquote>{file.snippets.slice(0,3).map((text,i)=><p key={i}>{text}</p>)}</blockquote>}
    {!primaryIsNamed&&<PreviewRows label="원본에서 읽은 표" rows={file.preview} columns={file.columns}/>}
    {tables.map((table,index)=><div key={table.block_id || index}>
      <PreviewRows label={table.name} columns={table.columns} rows={table.preview}/>
      {tableSelection&&table.block_id&&table.preview.length>0&&
        <DocumentTableColumns fileId={file.id} fileName={file.name} table={table} selection={tableSelection}/>}
    </div>)}
    {details&&<button type="button" className="as-text-button" onClick={details.open}>{details.label}</button>}
    {file.fields.length>0&&<dl>{file.fields.map(field=><div key={field.label}>
      <dt>{field.label}</dt><dd>{field.value == null?'여러 값이 확인됨':String(field.value)}</dd>
    </div>)}</dl>}
  </details>;
}

export type FileFacts = {row_count?:number; page_count?:number};

function listed(names:string[]) {
  return names.slice(0,3).join(', ')+(names.length>3?' 등':'');
}

/** One sentence per file from what the parser actually read; nothing is guessed or inferred. */
export function describeFile(file:InspectedFile, facts:FileFacts={}):string {
  const label='「'+file.name+'」';
  const tables=file.tables || [];
  if (!file.has_readable_text && !file.columns.length && !tables.length)
    return label+'에서 읽어 낸 글자가 없어요. 원본을 확인하거나 더 선명한 파일을 연결해 주세요.';
  const parts:string[]=[];
  if (file.kind==='table' || file.columns.length || tables.length) {
    parts.push(label+' 파일은 표 자료예요.');
    if (tables.length>1) parts.push('시트 '+tables.length+'개('+listed(tables.map(table=>table.name))+')가 있어요.');
    const columns=file.columns.length ? file.columns : (tables[0]?.columns || []);
    const read=[columns.length ? '열 '+columns.length+'개('+listed(columns)+')' : '', facts.row_count ? facts.row_count+'행' : ''].filter(Boolean);
    // "120행을" / "열 5개(…)를": the object particle follows the last item read.
    if (read.length) parts.push(read.join(', ')+(facts.row_count?'을':'를')+' 읽었어요.');
  } else {
    parts.push(label+' 파일은 문서 자료예요.');
    if (facts.page_count) parts.push(facts.page_count+'쪽을 읽었어요.');
  }
  const fields=file.fields.map(field=>field.label);
  if (fields.length) parts.push('찾은 항목: '+listed(fields)+'.');
  if (file.partial) parts.push('일부 내용은 원본과 대조가 필요해요.');
  return parts.join(' ');
}

export function FileInspection({files,initial,fresh=[],readOnly,detailsFor,factsFor,selection}:{
  files:InspectedFile[];initial:boolean;readOnly:boolean;detailsFor:(id:string)=>FileDetails|undefined;
  selection?:DocumentTableSelection;
  factsFor?:(id:string)=>FileFacts|undefined;
  /** Files attached during the conversation and not yet used by a message. */
  fresh?:string[]
}) {
  if(!files.length)return null;
  // Before the first message every file is new; later only the files attached since the last message.
  const described=initial?files:files.filter(file=>fresh.includes(file.id));
  return <section className="as-file-inspection" aria-label="연결 파일 확인">
    <h3>연결한 파일에서 확인한 내용</h3>
    {described.length>0&&!readOnly&&<div className="as-file-summary" role="status">
      {described.map(file=><p key={file.id}>{describeFile(file,factsFor?.(file.id))}</p>)}
    </div>}
    {files.map(file=><InspectedSource key={file.id} file={file} open={initial&&!readOnly}
      details={detailsFor(file.id)} selection={readOnly?undefined:selection}/>)}
  </section>;
}

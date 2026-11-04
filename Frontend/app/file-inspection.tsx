import {useState} from 'react';

type Cell = string | number | boolean | null;
type InspectedTable = {name:string; columns:string[]; preview:Record<string,Cell>[]};
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
function InspectedSource({file,open,readOnly,disabled,onUse,details}:{
  file:InspectedFile;open:boolean;readOnly:boolean;disabled:boolean;onUse:(message:string)=>void;details?:FileDetails
}) {
  const [selected,setSelected]=useState<string[]>([]),[custom,setCustom]=useState('');
  const tables=file.tables || [];
  const primaryIsNamed=tables.some(table=>file.columns.length===table.columns.length &&
    file.columns.every((column,i)=>column===table.columns[i]) && file.preview.length===table.preview.length &&
    file.preview.every((row,i)=>file.columns.every(column=>{
      const a=row[column],b=table.preview[i][column];
      return a===b || (a!=null && b!=null && String(a)===String(b));
    })));
  const choices=[...new Set([...(primaryIsNamed?[]:file.columns),...file.fields.map(field=>field.label)])];
  const values=new Map(file.fields.map(field=>[field.label,field.value]));
  const fields=[...new Set([...selected,...custom.split(',').map(value=>value.trim()).filter(Boolean)])];
  function choose(value:string,checked:boolean) {
    setSelected(prior=>checked?[...prior,value]:prior.filter(entry=>entry!==value));
  }
  return <details open={open} className="as-inspected-source">
    <summary>{file.name} · {file.kind==='table'?'표':'문서'} 미리보기</summary>
    {file.partial&&<p>일부 내용은 확인이 필요해요. 원본과 대조해 주세요.</p>}
    {!file.has_readable_text && !file.columns.length&&!tables.length&&<p>읽어 낸 글자가 없어요. 원본을 확인하거나 더 선명한 파일을 연결해 주세요.</p>}
    {file.snippets.length>0&&<blockquote>{file.snippets.slice(0,3).map((text,i)=><p key={i}>{text}</p>)}</blockquote>}
    {!primaryIsNamed&&<PreviewRows label="원본에서 읽은 표" rows={file.preview} columns={file.columns}/>}
    {tables.map((table,index)=><div key={index}>
      <PreviewRows label={table.name} columns={table.columns} rows={table.preview}/>
      {!readOnly&&<fieldset disabled={disabled} className="as-file-fields">
        <legend>{table.name}의 열 선택</legend>
        <div className="as-receipt-options">{table.columns.map(column=>{
          const scoped='「'+table.name+'」의 '+column;
          return <label key={column}><input type="checkbox" checked={selected.includes(scoped)}
            onChange={event=>choose(scoped,event.target.checked)}/>{column}</label>;
        })}</div>
      </fieldset>}
    </div>)}
    {details&&<button type="button" className="as-text-button" onClick={details.open}>{details.label}</button>}
    {readOnly ? <dl>{file.fields.map(field=><div key={field.label}><dt>{field.label}</dt><dd>{field.value == null?'여러 값이 확인됨':String(field.value)}</dd></div>)}</dl> :
     <fieldset disabled={disabled} className="as-file-fields">
       <legend>요청에 담을 항목</legend>
       <div className="as-receipt-options">{choices.map(choice=><label key={choice}>
         <input type="checkbox" checked={selected.includes(choice)} onChange={event=>choose(choice,event.target.checked)}/>
         {choice}{values.has(choice) && ' · '+(values.get(choice)==null?'여러 값이 확인됨':String(values.get(choice)))}
       </label>)}</div>
       <label className="as-field">직접 지정할 항목<input value={custom} maxLength={500} onChange={event=>setCustom(event.target.value)}/></label>
       <button type="button" className="as-button" disabled={!fields.length}
         onClick={()=>onUse(file.name+'에서 '+fields.join(', ')+' 항목을 추출해 주세요.')}>요청에 추가</button>
     </fieldset>}
  </details>;
}

export function FileInspection({files,initial,readOnly,disabled,onUse,detailsFor}:{
  files:InspectedFile[];initial:boolean;readOnly:boolean;disabled:boolean;onUse:(message:string)=>void;detailsFor:(id:string)=>FileDetails|undefined
}) {
  if(!files.length)return null;
  return <section className="as-file-inspection" aria-label="연결 파일 확인">
    <h3>연결한 파일에서 확인한 내용</h3>
    {initial&&!readOnly&&<p>미리보기를 보고 필요한 항목을 선택하거나, 아래 입력창에 원하는 작업을 적어 주세요.</p>}
    {files.map(file=><InspectedSource key={file.id} file={file} open={initial&&!readOnly} readOnly={readOnly} disabled={disabled} onUse={onUse} details={detailsFor(file.id)}/>)}
  </section>;
}

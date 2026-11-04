import {useRef, useState} from 'react';
import {Download} from 'lucide-react';
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '@/components/ui/dialog';

type DownloadFile = {name:string; mime?:string; content?:string; encoding?:'base64'};
type FilePicker = (options:{suggestedName:string; types?:{description:string; accept:Record<string,string[]>}[]}) =>
  Promise<{createWritable():Promise<FileSystemWritableFileStream>}>;

const formats:Record<string, [string,string]> = {
  xlsx:['엑셀','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
  csv:['CSV','text/csv'], json:['JSON','application/json'], pdf:['PDF','application/pdf'],
  docx:['워드','application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
  md:['마크다운','text/markdown'], txt:['텍스트','text/plain'], png:['PNG','image/png'],
  html:['HTML','text/html'], zip:['ZIP','application/zip'], eml:['메일','message/rfc822'],
};

/** Older run artifacts use their storage UUID as a filename. Keep names chosen by the workflow. */
export function suggestedRunFilename(name:string, workflowName?:string) {
  const basename = name.split(/[\\/]/).pop() || '결과';
  const generated = basename.match(/^(?:[a-f\d]{32}|[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12})(\.[a-z\d]+)$/i);
  const stem = workflowName?.replace(/[<>:"/\\|?*\u0000-\u001f]/g, ' ').trim().slice(0, 120);
  return generated && stem ? stem+' 결과'+generated[1] : basename;
}

async function fileContents(file:DownloadFile, url?:string) {
  if (!url) {
    if (typeof file.content !== 'string') throw new Error('저장할 파일 내용이 없습니다.');
    return new Blob([file.encoding === 'base64'
      ? Uint8Array.from(atob(file.content), char => char.charCodeAt(0)) : file.content], {type:file.mime});
  }
  const response = await fetch(url, {credentials:'same-origin'});
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('a4b:unauthenticated'));
    const details = await response.json().catch(() => null);
    throw new Error(details?.error || '파일을 불러오지 못했어요. 다시 시도해 주세요.');
  }
  return response.blob();
}

export function RunFileDownload({file,url,label}:{file:DownloadFile; url?:string; label?:string}) {
  const filename = file.name.split(/[\\/]/).pop() || '결과';
  const suffix = filename.match(/\.[a-z0-9]{1,12}$/i)?.[0] || '';
  const format = formats[suffix.slice(1).toLowerCase()];
  const title = label || (format ? format[0]+' 파일 다운로드' : '결과 파일 다운로드');
  const [open,setOpen] = useState(false), [stem,setStem] = useState('');
  const [busy,setBusy] = useState(false), [error,setError] = useState('');
  const pending = useRef(false);
  const valid = !!stem.trim() && !/[<>:"/\\|?*\u0000-\u001f]/.test(stem);

  const downloadName = stem.trim().toLowerCase().endsWith(suffix.toLowerCase()) ? stem.trim() : stem.trim()+suffix;

  async function save(usingBrowserDownload=false) {
    if (pending.current || (usingBrowserDownload && !valid)) return;
    pending.current = true; setBusy(true); setError('');
    try {
      const picker = (window as Window & {showSaveFilePicker?:FilePicker}).showSaveFilePicker;
      if (!usingBrowserDownload) {
        if (picker) {
          let handle;
          try {
            handle = await picker.call(window, {suggestedName:filename,
              ...(format ? {types:[{description:format[0]+' 파일',accept:{[format[1]]:[suffix.toLowerCase()]}}]} : {})});
          } catch (cause) {
            if ((cause as Error).name === 'AbortError') return;
            if (!['SecurityError','NotSupportedError'].includes((cause as Error).name)) throw cause;
          }
          if (handle) {
            const blob = await fileContents(file,url);
            const stream = await handle.createWritable();
            try { await stream.write(blob); await stream.close(); }
            catch (cause) { await stream.abort().catch(()=>{}); throw cause; }
            return;
          }
        }
        setStem(suffix ? filename.slice(0,-suffix.length) : filename);
        setOpen(true);
        return;
      }
      const blob = await fileContents(file,url);
      const objectUrl = URL.createObjectURL(blob);
      try {
        const link = document.createElement('a');
        link.href=objectUrl; link.download=downloadName; link.target='_blank'; link.rel='noopener';
        document.body.appendChild(link);
        try { link.click(); } finally { link.remove(); }
      } finally { window.setTimeout(()=>URL.revokeObjectURL(objectUrl),1000); }
      setOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '파일을 저장하지 못했어요.');
    } finally { pending.current=false; setBusy(false); }
  }

  return <>
    <button type="button" className="as-button" onClick={()=>void save()} disabled={busy} title={filename}>
      <Download size={16}/>{title}
    </button>
    {!open && error && <p role="alert">{error}</p>}
    <Dialog open={open} onOpenChange={next=>{if(!pending.current)setOpen(next);}}>
      <DialogContent className="as-dialog">
        <DialogHeader><DialogTitle>파일 저장</DialogTitle>
          <DialogDescription>파일 이름을 정해 주세요. 저장 위치는 브라우저의 다운로드 설정을 따릅니다.</DialogDescription>
        </DialogHeader>
        <form onSubmit={event=>{event.preventDefault();void save(true);}}>
          <label className="as-field">파일 이름
            <input autoFocus value={stem} onChange={event=>setStem(event.target.value)} disabled={busy} maxLength={180}/>
          </label>
          {suffix && <p className="as-small-muted">저장할 파일: {downloadName}</p>}
          {error && <p role="alert">{error}</p>}
          <button type="submit" className="as-button as-primary" disabled={busy||!valid}>저장</button>
        </form>
      </DialogContent>
    </Dialog>
  </>;
}

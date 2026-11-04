import {useEffect, useRef, useState} from 'react';
import {AlertCircle, ExternalLink, FolderOpen, RefreshCw, Trash2, Upload as UploadIcon} from 'lucide-react';
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '@/components/ui/dialog';
import {request} from '../components/flow/connection-panel';

type Usage = {kind: 'draft' | 'workflow' | 'run' | 'schedule'; id: string; name: string};
type Upload = {
  id: string; name: string; format?: string; size_bytes: number | null; created_at?: string;
  has_original: boolean; pending_delete: boolean;
  usages: Usage[];
};
type Page = {assets: Upload[]; next: string | null};
type Preview = {text: string; columns: string[]; rows: Record<string, string>[]};
const base = '/api/agent-builder/';

function uploadInfo(asset: Upload) {
  const bytes = asset.size_bytes;
  return [asset.format?.toUpperCase(), bytes == null ? null : bytes < 1024 ? bytes + ' B' :
    bytes < 1024 * 1024 ? Math.ceil(bytes / 1024) + ' KB' : (bytes / 1024 / 1024).toFixed(1) + ' MB',
    asset.created_at && new Date(asset.created_at).toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'})]
    .filter(Boolean).join(' · ');
}

function UploadPreview({asset, close}: {asset: Upload; close: () => void}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState('');
  const originalUrl = base + 'original?id=' + encodeURIComponent(asset.id);
  useEffect(() => {
    let current = true;
    request<Preview>(base + 'asset_preview?id=' + encodeURIComponent(asset.id))
      .then(result => {if (current) setPreview(result);})
      .catch(e => {if (current) setError(e.message);});
    return () => {current = false;};
  }, [asset.id]);
  return <Dialog open onOpenChange={open => {if (!open) close();}}>
    <DialogContent className="as-dialog"><DialogHeader><DialogTitle>{asset.name}</DialogTitle>
      <DialogDescription>자료에서 읽은 내용의 일부입니다.</DialogDescription></DialogHeader>
      {asset.has_original && ['jpg','jpeg','png','webp','bmp'].includes(asset.format || '') &&
        <img className="upload-image-preview" src={originalUrl} alt={asset.name + ' 원본'}/>}
      {error ? <p role="alert">{error}</p> : !preview ? <p role="status">내용을 불러오는 중입니다.</p> :
        <>{!!preview.columns.length && <div className="as-table-scroll"><table>
          <thead><tr>{preview.columns.map(col => <th key={col}>{col}</th>)}</tr></thead>
          <tbody>{preview.rows.map((row, i) => <tr key={i}>{preview.columns.map(col => <td key={col}>{row[col]}</td>)}</tr>)}</tbody>
        </table></div>}
        {preview.text && <p className="as-preserve">{preview.text}</p>}
        {!preview.text && !preview.columns.length && <p>미리 볼 수 있는 텍스트가 없습니다.</p>}</>}
      {asset.has_original && <a className="as-button" href={originalUrl}
        target="_blank" rel="noopener noreferrer">원본 열기 <ExternalLink size={14}/></a>}
    </DialogContent>
  </Dialog>;
}

function useUploads() {
  const [items, setItems] = useState<Upload[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const generation = useRef(0);

  async function load(before?: string | null) {
    const current = ++generation.current;
    setLoading(true); setError('');
    try {
      const page = await request<Page>(base + 'assets' + (before ? '?before=' + encodeURIComponent(before) : ''));
      if (current !== generation.current) return;
      setItems(previous => before ? [...previous, ...page.assets] : page.assets);
      setNext(page.next);
    } catch (e) {
      if (current === generation.current) setError((e as Error).message);
    } finally {if (current === generation.current) setLoading(false);}
  }
  useEffect(() => {void load(); return () => {generation.current++;};}, []);
  return {items, setItems, loading, error, setError, load, next};
}

function UploadChoices({connected, formats, onSelect}: {connected: string[]; formats?: string; onSelect: (id: string) => void}) {
  const {items, loading, error, load, next} = useUploads();
  const choices = items.filter(item => !item.pending_delete &&
    (!formats || formats.split(',').includes('.' + (item.format || item.name.split('.').pop())?.toLowerCase())));
  return <>
    {choices.map(item => <button type="button" className="as-resource" key={item.id}
      disabled={connected.includes(item.id)} onClick={() => onSelect(item.id)}>
      <span><strong>{item.name}</strong><small>{uploadInfo(item)}</small></span>
      {connected.includes(item.id) && <small>이미 연결됨</small>}
    </button>)}
    {loading && <p role="status">자료를 불러오는 중입니다.</p>}
    {error && <p role="alert">{error} <button type="button" className="as-button" onClick={() => void load(next)}>다시 시도</button></p>}
    {!loading && !error && !choices.length && <p>{next ? '이 페이지에는 연결할 수 있는 자료가 없습니다.' : '선택할 수 있는 자료가 없습니다.'}</p>}
    {next && !error && <button type="button" className="as-button" disabled={loading} onClick={() => void load(next)}>자료 더 보기</button>}
  </>;
}

export function AttachUploadDialog({mode, onLibrary, onClose, onNew, onSelect, connected, formats}: {
  mode: 'choose' | 'library'; onLibrary: () => void; onClose: () => void; onNew: () => void;
  onSelect: (id: string) => void; connected: string[]; formats?: string;
}) {
  return <Dialog open onOpenChange={open => {if (!open) onClose();}}>
    <DialogContent className="as-dialog"><DialogHeader><DialogTitle>{mode === 'choose' ? '자료 연결' : '내 자료에서 선택'}</DialogTitle>
      <DialogDescription>{connected.length >= 10 ? '한 설계에 연결할 수 있는 자료는 최대 10개입니다.' :
        mode === 'choose' ? '이 설계에서 사용할 파일을 선택하세요.' : '파일을 선택하면 현재 설계에 연결됩니다.'}</DialogDescription></DialogHeader>
      {connected.length < 10 && (mode === 'choose'
        ? <div className="as-run-actions">
          <button type="button" className="as-button" onClick={onNew}><UploadIcon size={16}/>새 파일 올리기</button>
          <button type="button" className="as-button" onClick={onLibrary}><FolderOpen size={16}/>내 자료에서 선택</button>
        </div> : <UploadChoices connected={connected} formats={formats} onSelect={onSelect}/>)}
    </DialogContent>
  </Dialog>;
}

const usageLabels: Record<Usage['kind'], string> = {
  draft: '설계 중', workflow: '저장된 워크플로우', run: '실행 기록', schedule: '예약 데이터 소스',
};

export function UploadLibrary() {
  const {items, setItems, next, loading, error, setError, load} = useUploads();
  const [deleting, setDeleting] = useState('');
  const [selected, setSelected] = useState<Upload | null>(null);
  const [notice, setNotice] = useState('');

  async function remove(asset: Upload) {
    if (!asset.pending_delete && !window.confirm('「' + asset.name + '」 자료를 삭제할까요? 설계 중인 대화에서도 연결이 해제되며 복구할 수 없습니다.')) return;
    setDeleting(asset.id); setError(''); setNotice('');
    try {
      await request(base + 'assets?id=' + encodeURIComponent(asset.id), undefined, 'DELETE');
      setItems(previous => previous.filter(item => item.id !== asset.id));
      setNotice(asset.name + ' 자료를 삭제했습니다.');
    } catch (e) {
      if (e instanceof Error && (e.cause as {pending_delete?: boolean} | null)?.pending_delete) {
        setItems(previous => previous.map(item => item.id === asset.id ? {...item, pending_delete: true, usages: []} : item));
      }
      setError((e as Error).message);
    } finally {setDeleting('');}
  }
  const busy = loading || !!deleting;
  return <>
    <div className="entry-page-heading"><div><h1>내 자료</h1><p>워크플로우를 설계하거나 실행할 때 올린 파일을 확인하고 관리하세요.</p></div>
      <button className="as-button" onClick={() => {setNotice(''); void load();}} disabled={busy} aria-label="자료 목록 새로고침"><RefreshCw size={16}/></button>
    </div>
    {error && <div className="as-error" role="alert"><AlertCircle size={16}/>{error}</div>}
    {notice && <p role="status">{notice}</p>}
    {loading && <p className="entry-loading" role="status">자료를 불러오는 중입니다.</p>}
    {!loading && !items.length && !error && <section className="entry-empty"><FolderOpen size={26}/>
      <h2>아직 올린 자료가 없습니다.</h2><p>설계 화면에서 ‘자료 연결’로 파일을 올리면 여기에 표시됩니다.</p></section>}
    <div className="entry-history-list">{items.map(asset => {
      const usages = asset.usages || [];
      const protectedFile = usages.some(use => use.kind !== 'draft');
      return <article key={asset.id}>
      <div><h2>{asset.name}</h2><p>{uploadInfo(asset)}{asset.pending_delete && ' · 삭제 대기 중'}</p>
        {usages.length ? <ul aria-label="자료 사용처">{usages.map(use => <li key={use.kind + use.id}>
          {use.name} · {usageLabels[use.kind]}</li>)}</ul> : !asset.pending_delete && <p>연결된 사용처 없음</p>}
      </div>
      <button className="as-button" disabled={busy || asset.pending_delete} onClick={() => setSelected(asset)}>내용 보기</button>
      <button className="as-button" disabled={busy || protectedFile} aria-label={asset.name + ' 삭제'} onClick={() => void remove(asset)}>
        <Trash2 size={14}/>{deleting === asset.id ? '삭제 중…' : asset.pending_delete ? '삭제 재시도' : protectedFile ? '사용 중 · 삭제 불가' : '삭제'}
      </button>
    </article>;})}</div>
    {next && <button className="as-button" disabled={busy} onClick={() => void load(next)}>자료 더 보기</button>}
    {selected && <UploadPreview key={selected.id} asset={selected} close={() => setSelected(null)}/>}
  </>;
}

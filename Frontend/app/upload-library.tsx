import {useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode} from 'react';
import {AlertCircle, Check, ChevronRight, ExternalLink, FileText, Folder, FolderInput, FolderOpen, FolderPlus, Pencil, RefreshCw, Search, Trash2, Upload as UploadIcon, X} from 'lucide-react';
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '@/components/ui/dialog';
import {request} from '../components/flow/connection-panel';
import {useAuth} from './auth';
import {TeamLibraryControls, teamLibraryBase, useTeamSpaces, type TeamSummary} from './team-library';
import './asset-library.css';

type Usage = {kind: 'draft' | 'workflow' | 'run' | 'running' | 'schedule'; id: string; name: string};
type Upload = {
  id: string; name: string; format?: string; size_bytes: number | null; created_at?: string; folder_id?: string | null;
  has_original: boolean; pending_delete: boolean;
  processing_status?: 'ready' | 'partial';
  usages: Usage[];
};
type Page = {assets: Upload[]; next: string | null};
type LibraryFolder = {id: string; name: string; parent_id: string | null};
type LibraryPage = Page & {space: {id: string; kind: 'personal' | 'team'; name: string}; folder_id: string | null; folders: LibraryFolder[]};
type Preview = {text: string; columns: string[]; rows: Record<string, string>[]};
const base = '/api/agent-builder/';

function uploadInfo(asset: Upload) {
  const bytes = asset.size_bytes;
  return [asset.format?.toUpperCase(), bytes == null ? null : bytes < 1024 ? bytes + ' B' :
    bytes < 1024 * 1024 ? Math.ceil(bytes / 1024) + ' KB' : (bytes / 1024 / 1024).toFixed(1) + ' MB',
    asset.created_at && new Date(asset.created_at).toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'})]
    .filter(Boolean).join(' · ');
}

function UploadPreview({asset, close, endpoint = base}: {asset: Upload; close: () => void; endpoint?: string}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState('');
  const originalUrl = endpoint + 'original?id=' + encodeURIComponent(asset.id);
  useEffect(() => {
    let current = true;
    request<Preview>(endpoint + 'asset_preview?id=' + encodeURIComponent(asset.id))
      .then(result => {if (current) setPreview(result);})
      .catch(e => {if (current) setError(e.message);});
    return () => {current = false;};
  }, [asset.id, endpoint]);
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

function useUploads(endpoint = base) {
  const [items, setItems] = useState<Upload[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const generation = useRef(0);

  async function load(before?: string | null) {
    const current = ++generation.current;
    setLoading(true); setError('');
    try {
      const page = await request<Page>(endpoint + 'assets' + (before ? '?before=' + encodeURIComponent(before) : ''));
      if (current !== generation.current) return;
      setItems(previous => before ? [...previous, ...page.assets] : page.assets);
      setNext(page.next);
    } catch (e) {
      if (current === generation.current) setError((e as Error).message);
    } finally {if (current === generation.current) setLoading(false);}
  }
  useEffect(() => {setItems([]); setNext(null); void load(); return () => {generation.current++;};}, [endpoint]);
  return {items, setItems, loading, error, setError, load, next};
}

function UploadChoices({connected, formats, onSelect}: {connected: string[]; formats?: string; onSelect: (id: string) => void}) {
  const auth = useAuth(), teamEnabled = auth.enabled && !!auth.user && !auth.loading, spaces = useTeamSpaces(teamEnabled);
  const [teamId, setTeamId] = useState(''), [copying, setCopying] = useState(false), [copyError, setCopyError] = useState('');
  const {items, loading, error, load, next} = useUploads(teamLibraryBase(teamId));
  const current = useRef(true);
  useEffect(() => {current.current = true; return () => {current.current = false;};}, []);
  async function select(id: string) {
    if (copying) return;
    if (!teamId) {onSelect(id); return;}
    setCopying(true); setCopyError('');
    try {
      const result = await request<{asset: {id: string}}>(teamLibraryBase(teamId) + 'import',
        {asset_id: id});
      if (current.current) onSelect(result.asset.id);
    } catch (e) {if (current.current) setCopyError((e as Error).message);}
    finally {if (current.current) setCopying(false);}
  }
  const choices = items.filter(item => !item.pending_delete &&
    (!formats || formats.split(',').includes('.' + (item.format || item.name.split('.').pop())?.toLowerCase())));
  return <>
    {teamEnabled && <label>자료 공간 <select className="as-input" aria-label="연결할 자료 공간" value={teamId} disabled={copying}
      onChange={event => {setTeamId(event.target.value); setCopyError('');}}>
      <option value="">내 자료</option>{spaces.teams.map(team => <option key={team.id} value={team.id}>{team.name}</option>)}
    </select></label>}
    {spaces.error && <p role="alert">{spaces.error}</p>}
    {teamId && <p className="as-muted">팀 파일의 원본을 개인 자료함에 복사해 설계에 연결합니다. 사본은 팀의 변경과 별개로 보관됩니다.</p>}
    {copying && <p role="status">팀 자료를 복사해 연결하는 중입니다.</p>}
    {copyError && <p role="alert">{copyError}</p>}
    {choices.map(item => <button type="button" className="as-resource" key={item.id}
      disabled={copying || (!teamId && connected.includes(item.id))} onClick={() => void select(item.id)}>
      <span><strong>{item.name}</strong><small>{uploadInfo(item)}</small></span>
      {!teamId && connected.includes(item.id) && <small>이미 연결됨</small>}
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
    <DialogContent className="as-dialog"><DialogHeader><DialogTitle>{mode === 'choose' ? '자료 연결' : '자료함에서 선택'}</DialogTitle>
      <DialogDescription>{connected.length >= 10 ? '한 설계에 연결할 수 있는 자료는 최대 10개입니다.' :
        mode === 'choose' ? '이 설계에서 사용할 파일을 선택하세요.' : '파일을 선택하면 현재 설계에 연결됩니다.'}</DialogDescription></DialogHeader>
      {connected.length < 10 && (mode === 'choose'
        ? <div className="as-run-actions">
          <button type="button" className="as-button" onClick={onNew}><UploadIcon size={16}/>새 파일 올리기</button>
          <button type="button" className="as-button" onClick={onLibrary}><FolderOpen size={16}/>자료함에서 선택</button>
        </div> : <UploadChoices connected={connected} formats={formats} onSelect={onSelect}/>)}
    </DialogContent>
  </Dialog>;
}

const usageLabels: Record<Usage['kind'], string> = {
  draft: '설계 중', workflow: '저장된 워크플로우', run: '실행 기록', running: '실행 중', schedule: '예약에 사용하는 파일',
};

function useLibrary(endpoint: string, folderId: string | null, query: string) {
  const [items, setItems] = useState<Upload[]>([]);
  const [folders, setFolders] = useState<LibraryFolder[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const load = useCallback(async (before?: string | null) => {
    const current = ++generation.current;
    setLoading(true); setError('');
    if (!before) {setItems([]); setNext(null);}
    const params = new URLSearchParams();
    if (folderId) params.set('folder_id', folderId);
    if (query) params.set('q', query);
    if (before) params.set('before', before);
    try {
      const page = await request<LibraryPage>(endpoint + 'library' + (params.size ? '?' + params : ''));
      if (current !== generation.current) return;
      setItems(previous => before ? [...new Map([...previous, ...page.assets].map(item => [item.id, item])).values()] : page.assets);
      setFolders(page.folders); setNext(page.next);
    } catch (e) {if (current === generation.current) setError((e as Error).message);}
    finally {if (current === generation.current) setLoading(false);}
  }, [endpoint, folderId, query]);
  useEffect(() => {void load(); return () => {generation.current++;};}, [load]);
  return {items, setItems, folders, next, loading, error, setError, load};
}

function folderTrail(id: string | null, folders: LibraryFolder[]) {
  const trail: LibraryFolder[] = [], seen = new Set<string>();
  while (id && !seen.has(id)) {
    seen.add(id);
    const folder = folders.find(item => item.id === id);
    if (!folder) break;
    trail.unshift(folder); id = folder.parent_id;
  }
  return trail;
}
const acceptedFormats = '.csv,.tsv,.xlsx,.xls,.json,.txt,.md,.pdf,.doc,.docx,.ppt,.pptx,.hwp,.hwpx,.jpg,.jpeg,.png,.bmp,.webp,.tif,.tiff,.heic';
type UploadTask = {id: number; file: File; folderId: string | null; state: 'pending' | 'uploading' | 'done' | 'error'; error?: string; retryable?: boolean};
type LibraryAction = {kind: 'create'; parentId: string | null} | {kind: 'rename' | 'move-folder' | 'delete'; folder: LibraryFolder} | {kind: 'move-asset'; asset: Upload};

async function fileBase64(file: File) {
  return await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = () => reject(new Error('파일을 읽지 못했습니다. 다시 선택해주세요.'));
    reader.readAsDataURL(file);
  });
}

export function UploadLibrary({onCanLeaveChange}: {onCanLeaveChange?: (canLeave: () => boolean) => void} = {}) {
  const [team, setTeam] = useState<TeamSummary | null>(null), [busy, setBusy] = useState(false);
  const guard = useRef<() => boolean>(() => true);
  const registerGuard = useCallback((canLeave: () => boolean) => {guard.current = canLeave; onCanLeaveChange?.(canLeave);}, [onCanLeaveChange]);
  const selectTeam = useCallback((next: TeamSummary | null) => {if (guard.current()) setTeam(next);}, []);
  return <LibraryContents key={team?.id || 'personal'} team={team} onBusyChange={setBusy} onCanLeaveChange={registerGuard} spaceControls={<TeamLibraryControls selected={team} onSelect={selectTeam} disabled={busy}/>}/>;
}

function LibraryContents({team, onBusyChange, onCanLeaveChange, spaceControls}: {spaceControls:ReactNode; team: TeamSummary | null; onBusyChange: (busy: boolean) => void; onCanLeaveChange: (canLeave: () => boolean) => void}) {
  const endpoint = teamLibraryBase(team?.id), rootName = team?.name || '내 자료', canEdit = !team || team.role !== 'viewer';
  const [folderId, setFolderId] = useState<string | null>(null);
  const [query, setQuery] = useState(''), [search, setSearch] = useState('');
  const {items, setItems, folders, next, loading, error, setError, load} = useLibrary(endpoint, folderId, query);
  const [deleting, setDeleting] = useState('');
  const [checked,setChecked]=useState<string[]>([]);
  useEffect(()=>setChecked([]),[folderId,query]);
  const [selected, setSelected] = useState<Upload | null>(null);
  const [notice, setNotice] = useState('');
  const [action, setAction] = useState<LibraryAction | null>(null);
  const [name, setName] = useState(''), [destination, setDestination] = useState('');
  const [saving, setSaving] = useState(false), [actionError, setActionError] = useState('');
  const [tasks, setTasks] = useState<UploadTask[]>([]), [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null), queue = useRef<UploadTask[]>([]), taskId = useRef(0);
  const running = useRef(false), writing = useRef(false), mounted = useRef(true), reload = useRef(load);
  reload.current = load;
  useEffect(() => {mounted.current = true; return () => {mounted.current = false;};}, []);
  const busy = loading || !!deleting || saving || uploading;
  const mutating = !!deleting || saving || uploading;
  useEffect(() => {onBusyChange(busy);}, [busy, onBusyChange]);
  const canLeave = useCallback(() => {
    if (!mounted.current) return true;
    if (running.current || writing.current) {
      setNotice(running.current ? '파일을 올리는 중입니다. 모든 파일의 업로드가 끝난 뒤 이동할 수 있습니다.' : '자료를 변경하는 중입니다. 작업이 끝난 뒤 이동할 수 있습니다.');
      return false;
    }
    return true;
  }, []);
  useEffect(() => {
    onCanLeaveChange(canLeave);
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!running.current && !writing.current) return;
      event.preventDefault(); event.returnValue = '';
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => {window.removeEventListener('beforeunload', beforeUnload); onCanLeaveChange(() => true);};
  }, [canLeave, onCanLeaveChange]);
  const trail = folderTrail(folderId, folders);
  const children = folders.filter(folder => folder.parent_id === folderId && (!query || folder.name.toLocaleLowerCase().includes(query.toLocaleLowerCase())));

  function updateTasks(update: (previous: UploadTask[]) => UploadTask[]) {
    queue.current = update(queue.current);
    if (mounted.current) setTasks([...queue.current]);
  }
  async function processUploads() {
    if (running.current) return;
    running.current = true; setUploading(true); setNotice('');
    let successes = 0;
    try {
      while (mounted.current) {
        const task = queue.current.find(item => item.state === 'pending');
        if (!task) break;
        updateTasks(previous => previous.map(item => item.id === task.id ? {...item, state: 'uploading', error: undefined} : item));
        try {
          const data = await fileBase64(task.file);
          // An account change can unmount this queue while the browser reads a file.
          if (!mounted.current) break;
          await request(endpoint + 'library_upload', {fileName: task.file.name, data, folder_id: task.folderId});
          successes++;
          updateTasks(previous => previous.map(item => item.id === task.id ? {...item, state: 'done'} : item));
        } catch (e) {
          updateTasks(previous => previous.map(item => item.id === task.id ? {...item, state: 'error', error: (e as Error).message, retryable: true} : item));
        }
      }
      if (successes && mounted.current) {
        setNotice(successes + '개 파일을 자료함에 올렸습니다.');
        void reload.current();
      }
    } finally {running.current = false; if (mounted.current) setUploading(false);}
  }
  function enqueue(files: File[]) {
    if (!canEdit || !files.length || running.current || writing.current || loading) return;
    updateTasks(previous => [...previous, ...files.map(file => {
      const format = '.' + file.name.split('.').pop()?.toLowerCase();
      const problem = file.size > 8 * 1024 * 1024 ? '파일은 8MB 이하로 올려주세요.' :
        !acceptedFormats.split(',').includes(format) ? '지원하는 문서·표·이미지 파일을 선택해주세요.' : '';
      return {id: ++taskId.current, file, folderId, state: problem ? 'error' as const : 'pending' as const, error: problem, retryable: false};
    })]);
    void processUploads();
  }
  function retryUpload(id?: number) {
    if (!canEdit || running.current || writing.current || loading) return;
    updateTasks(previous => previous.map(item => item.state === 'error' && item.retryable && (id === undefined || item.id === id) ? {...item, state: 'pending'} : item));
    void processUploads();
  }
  function navigate(id: string | null) {
    if (running.current || writing.current) return;
    setFolderId(id); setQuery(''); setSearch(''); setNotice('');
  }
  function openAction(value: LibraryAction) {
    setAction(value); setActionError('');
    setName(value.kind === 'rename' ? value.folder.name : '');
    setDestination(value.kind === 'move-folder' ? value.folder.parent_id || '' : value.kind === 'move-asset' ? value.asset.folder_id || '' : '');
  }
  async function saveAction(event: FormEvent) {
    event.preventDefault(); if (!canEdit || !action || writing.current || running.current) return;
    writing.current = true;
    setSaving(true); setActionError(''); setNotice('');
    try {
      if (action.kind === 'create') await request(endpoint + 'folders', {name: name.trim(), parent_id: action.parentId});
      else if (action.kind === 'rename') await request(endpoint + 'folders', {id: action.folder.id, name: name.trim()}, 'PATCH');
      else if (action.kind === 'move-folder') await request(endpoint + 'folders', {id: action.folder.id, parent_id: destination || null}, 'PATCH');
      else if (action.kind === 'move-asset') await request(endpoint + 'asset_move', {id: action.asset.id, folder_id: destination || null});
      else await request(endpoint + 'folders?id=' + encodeURIComponent(action.folder.id), undefined, 'DELETE');
      setAction(null);
      setNotice(action.kind === 'create' ? '폴더를 만들었습니다.' : action.kind === 'rename' ? '폴더 이름을 변경했습니다.' : action.kind === 'delete' ? '빈 폴더를 삭제했습니다.' : '자료 위치를 변경했습니다.');
      void reload.current();
    } catch (e) {setActionError((e as Error).message);}
    finally {writing.current = false; setSaving(false);}
  }
  const deletable = items.filter(asset => asset.pending_delete || !asset.usages?.some(use => use.kind === 'schedule' || use.kind === 'running'));
  const selectedFiles = deletable.filter(asset => checked.includes(asset.id));
  async function removeFiles(assets: Upload[]) {
    if (!canEdit || writing.current || running.current || !assets.length) return;
    if (!window.confirm(assets.map(asset => '「'+asset.name+'」').join(', ') +
      ' 자료를 삭제할까요? 연결된 설계 대화에서 해제됩니다. 기존 실행 결과는 보관되지만 다시 실행할 때 새 파일 연결이 필요할 수 있습니다. 삭제한 파일은 복구할 수 없습니다.')) return;
    writing.current = true; setDeleting('batch'); setError(''); setNotice('');
    const failures: string[] = [];
    let count = 0;
    try {
      for (const asset of assets) {
        try {
          await request(endpoint + 'assets?id=' + encodeURIComponent(asset.id), undefined, 'DELETE');
          setItems(previous => previous.filter(item => item.id !== asset.id));
          setChecked(previous => previous.filter(id => id !== asset.id));
          count++;
        } catch (e) {
          if (e instanceof Error && (e.cause as {pending_delete?: boolean} | null)?.pending_delete) {
            setItems(previous => previous.map(item => item.id === asset.id ? {...item, pending_delete: true, usages: []} : item));
          }
          failures.push(asset.name + ': ' + (e as Error).message);
        }
      }
      setNotice(count + '개 자료를 삭제했습니다.');
      if (failures.length) setError(failures.join('\n'));
    } finally {writing.current = false; setDeleting('');}
  }
  async function remove(asset: Upload) { await removeFiles([asset]); }
  const excluded = new Set<string>();
  if (action?.kind === 'move-folder') {
    excluded.add(action.folder.id);
    let changed = true;
    while (changed) {changed = false; for (const folder of folders) if (folder.parent_id && excluded.has(folder.parent_id) && !excluded.has(folder.id)) {excluded.add(folder.id); changed = true;}}
  }
  const actionTitle = action?.kind === 'create' ? '새 폴더' : action?.kind === 'rename' ? '폴더 이름 변경' : action?.kind === 'delete' ? '폴더 삭제' : action?.kind === 'move-folder' ? '폴더 이동' : '파일 이동';
  const moving = action?.kind === 'move-folder' || action?.kind === 'move-asset';
  const sameDestination = action?.kind === 'move-folder' ? destination === (action.folder.parent_id || '') : action?.kind === 'move-asset' ? destination === (action.asset.folder_id || '') : false;

  return <div className="asset-library">
    <div className="entry-page-heading"><div><h1>자료함</h1></div>
      <div className="asset-library-actions">
        {canEdit && <><button type="button" className="as-button as-primary" disabled={busy} onClick={() => input.current?.click()}><UploadIcon size={15}/>파일 올리기</button>
        <button type="button" className="as-button as-secondary" disabled={busy} onClick={() => openAction({kind: 'create', parentId: folderId})}><FolderPlus size={15}/>새 폴더</button></>}
        <button type="button" className="as-button" onClick={() => {setNotice(''); void load();}} disabled={busy} aria-label="목록 새로고침" title="목록 새로고침"><RefreshCw size={16}/></button>
      </div>
    </div>
    {spaceControls}
    <div className="asset-library-toolbar">
      <nav className="asset-breadcrumb" aria-label="자료함 위치"><button type="button" disabled={mutating} onClick={() => navigate(null)} aria-current={!folderId ? 'page' : undefined}><FolderOpen size={15}/>{rootName}</button>
        {trail.map(folder => <span key={folder.id}><ChevronRight size={13} aria-hidden="true"/><button type="button" disabled={mutating} onClick={() => navigate(folder.id)} aria-current={folder.id === folderId ? 'page' : undefined}>{folder.name}</button></span>)}
      </nav>
      <form className="asset-library-search" role="search" onSubmit={event => {event.preventDefault(); if (!running.current && !writing.current) setQuery(search.trim());}}>
        <input aria-label="현재 폴더에서 검색" placeholder="현재 폴더에서 검색" value={search} disabled={mutating} onChange={event => setSearch(event.target.value)}/>
        {search && <button type="button" disabled={mutating} aria-label="자료 검색 지우기" onClick={() => {setSearch(''); setQuery('');}}><X size={14}/></button>}
        <button type="submit" disabled={mutating} aria-label="자료 검색" title="검색"><Search size={16} aria-hidden="true"/></button>
      </form>
    </div>
    {canEdit && <><div className="asset-dropzone" role="region" data-dragging={dragging} aria-label="파일 업로드" onDragOver={event => {event.preventDefault(); if (!busy) setDragging(true);}}
      onDragLeave={() => setDragging(false)} onDrop={event => {event.preventDefault(); setDragging(false); if (!busy) enqueue(Array.from(event.dataTransfer.files));}}>
      <UploadIcon size={22} aria-hidden="true"/><div><strong>이곳에 파일을 끌어놓으세요</strong><span>문서·표·이미지 · 파일당 최대 8MB · {folderId ? trail.at(-1)?.name || '현재 폴더' : rootName}에 저장</span></div>
      <button type="button" className="as-text-button" disabled={busy} onClick={() => input.current?.click()}>파일 선택</button>
    </div>
    <input ref={input} type="file" hidden multiple disabled={busy} accept={acceptedFormats} aria-label="자료함에 올릴 파일" onChange={event => {const files = Array.from(event.currentTarget.files || []); event.currentTarget.value = ''; enqueue(files);}}/></>}
    {!!tasks.length && <section className="asset-upload-queue" aria-label="파일 업로드 진행">
      <div className="asset-section-heading"><h2>업로드 {tasks.filter(task => task.state === 'done').length}/{tasks.length}</h2>
        {!uploading && tasks.some(task => task.state === 'error' && task.retryable) && <button type="button" className="as-text-button" disabled={busy} onClick={() => retryUpload()}>실패한 파일 다시 시도</button>}
        {!uploading && <button type="button" className="as-text-button" onClick={() => updateTasks(() => [])}>목록 닫기</button>}</div>
      <ul>{tasks.map(task => <li key={task.id} data-state={task.state}>
        {task.state === 'done' ? <Check size={15} aria-hidden="true"/> : task.state === 'error' ? <AlertCircle size={15} aria-hidden="true"/> : <FileText size={15} aria-hidden="true"/>}
        <span><strong>{task.file.name}</strong>{task.error && <small role="alert">{task.error}</small>}</span>
        <small role="status">{task.state === 'done' ? '완료' : task.state === 'error' ? '실패' : task.state === 'uploading' ? '올리는 중…' : '대기 중'}</small>
        {task.state === 'error' && task.retryable && <button type="button" className="as-text-button" disabled={busy} aria-label={task.file.name + ' 업로드 다시 시도'} onClick={() => retryUpload(task.id)}>다시 시도</button>}
      </li>)}</ul>
    </section>}
    {error && <div className="as-error" role="alert"><AlertCircle size={16}/>{error}</div>}
    {notice && <p className="asset-library-notice" role="status">{notice}</p>}
    {loading && <p className="entry-loading" role="status">자료를 불러오는 중입니다.</p>}
    {!!children.length && <section aria-label="폴더"><div className="asset-section-heading"><h2>폴더</h2><span>{children.length}개</span></div><div className="asset-folder-grid">{children.map(folder => <article key={folder.id}>
      <button type="button" className="asset-folder-open" disabled={busy} onClick={() => navigate(folder.id)} aria-label={folder.name + ' 폴더 열기'}><Folder size={20}/><strong>{folder.name}</strong></button>
      {canEdit && <div className="asset-folder-actions"><button type="button" disabled={busy} aria-label={folder.name + ' 폴더 이름 변경'} onClick={() => openAction({kind: 'rename', folder})}><Pencil size={14}/></button>
        <button type="button" disabled={busy} aria-label={folder.name + ' 폴더 이동'} onClick={() => openAction({kind: 'move-folder', folder})}><FolderInput size={15}/></button>
        <button type="button" disabled={busy} aria-label={folder.name + ' 폴더 삭제'} onClick={() => openAction({kind: 'delete', folder})}><Trash2 size={14}/></button></div>}
    </article>)}</div></section>}
    {!loading && !items.length && !children.length && !error && <section className="entry-empty"><FolderOpen size={26}/>
      <h2>{query ? '검색 결과가 없습니다.' : '이 폴더에 자료가 없습니다.'}</h2><p>{query ? '다른 이름으로 검색하거나 검색어를 지워보세요.' : canEdit ? '파일을 올리거나 새 폴더를 만들어 자료를 정리하세요.' : '팀원이 자료를 올리면 이곳에서 확인할 수 있습니다.'}</p></section>}
    {!!items.length && <div className="asset-section-heading"><h2>파일</h2><span>{items.length}개{next ? ' 이상' : ''}</span></div>}
    {canEdit && !!items.length && <div className="asset-section-heading">
      <label className="asset-selection-label"><input type="checkbox" aria-label="삭제 가능한 파일 전체 선택" disabled={busy||!deletable.length}
        checked={!!deletable.length&&selectedFiles.length===deletable.length}
        onChange={event=>setChecked(event.target.checked?deletable.map(asset=>asset.id):[])}/>전체 선택</label>
      <span>{selectedFiles.length}개 선택</span>
      <button type="button" className="as-button" disabled={busy||!selectedFiles.length} onClick={()=>void removeFiles(selectedFiles)}><Trash2 size={14}/>선택 삭제</button>
    </div>}
    <div className="entry-history-list asset-file-list">{items.map(asset => {
      const usages = asset.usages || [];
      const blocked = usages.some(use => use.kind === 'schedule') ? '예약 파일 교체 후 삭제' : usages.some(use => use.kind === 'running') ? '실행 완료 후 삭제' : '';
      return <article key={asset.id}>
        {canEdit && <input type="checkbox" aria-label={asset.name+' 선택'} disabled={busy||(!asset.pending_delete&&!!blocked)}
          checked={checked.includes(asset.id)} onChange={event=>setChecked(previous=>event.target.checked?[...previous,asset.id]:previous.filter(id=>id!==asset.id))}/>}
        <FileText className="asset-file-icon" size={21} aria-hidden="true"/><div className="asset-file-info"><h2>{asset.name}</h2><p>{uploadInfo(asset)}{asset.processing_status === 'ready' ? ' · 사용 가능' : asset.processing_status === 'partial' ? ' · 일부 확인 필요' : ''}{asset.pending_delete && ' · 삭제 대기 중'}</p>
          {usages.length ? <ul aria-label="자료 사용처">{usages.map(use => <li key={use.kind + use.id}>{use.name} · {usageLabels[use.kind]}</li>)}</ul> : !asset.pending_delete && <p>연결된 사용처 없음</p>}
        </div>
        <div className="asset-file-actions"><button type="button" className="as-button as-secondary" disabled={busy || asset.pending_delete} onClick={() => setSelected(asset)}>내용 보기</button>
          {canEdit && <><button type="button" className="as-button" disabled={busy || asset.pending_delete} aria-label={asset.name + ' 이동'} onClick={() => openAction({kind: 'move-asset', asset})}><FolderInput size={14}/>이동</button>
          <button type="button" className="as-button" disabled={busy || (!asset.pending_delete && !!blocked)} aria-label={asset.name + ' 삭제'} onClick={() => void remove(asset)}><Trash2 size={14}/>{deleting === asset.id ? '삭제 중…' : asset.pending_delete ? '삭제 재시도' : blocked || '삭제'}</button></>}
        </div>
      </article>;
    })}</div>
    {next && <button type="button" className="as-button" disabled={busy} onClick={() => void load(next)}>자료 더 보기</button>}
    {selected && <UploadPreview key={selected.id} endpoint={endpoint} asset={selected} close={() => setSelected(null)}/>}
    {action && <Dialog open onOpenChange={open => {if (!open && !saving) setAction(null);}}><DialogContent className="as-dialog asset-action-dialog" showCloseButton={!saving}>
      <DialogHeader><DialogTitle>{actionTitle}</DialogTitle><DialogDescription>{action.kind === 'delete' ? '비어 있는 폴더만 삭제할 수 있습니다. 파일이 있다면 먼저 옮겨주세요.' : moving ? '파일 연결과 사용 기록은 유지됩니다. 정리할 위치를 선택하세요.' : '자료를 쉽게 찾을 수 있는 폴더 이름을 입력하세요.'}</DialogDescription></DialogHeader>
      <form onSubmit={event => void saveAction(event)}>
        {action.kind === 'create' || action.kind === 'rename' ? <label>폴더 이름<input autoFocus value={name} maxLength={80} disabled={saving} onChange={event => setName(event.target.value)}/></label> : moving ? <label>이동할 위치<select aria-label="이동할 위치" value={destination} disabled={saving} onChange={event => setDestination(event.target.value)}><option value="">{rootName}</option>{folders.filter(folder => !excluded.has(folder.id)).map(folder => <option key={folder.id} value={folder.id}>{rootName} / {folderTrail(folder.id, folders).map(item => item.name).join(' / ')}</option>)}</select></label> : <p>‘{action.folder.name}’ 폴더를 삭제할까요?</p>}
        {actionError && <p role="alert">{actionError}</p>}
        <div className="asset-dialog-actions"><button type="button" className="as-button" disabled={saving} onClick={() => setAction(null)}>취소</button><button type="submit" className="as-button as-primary" disabled={saving || (!moving && action.kind !== 'delete' && !name.trim()) || !!sameDestination}>{saving ? '저장 중…' : action.kind === 'create' ? '폴더 만들기' : action.kind === 'delete' ? '폴더 삭제' : moving ? '여기로 이동' : '이름 변경'}</button></div>
      </form>
    </DialogContent></Dialog>}
  </div>;
}

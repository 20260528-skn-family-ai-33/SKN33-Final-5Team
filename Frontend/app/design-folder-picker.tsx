import {useEffect, useRef, useState} from 'react';
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '@/components/ui/dialog';
import {request} from '../components/flow/connection-panel';
import {folderTrail, useLibrary, type LibraryFolder, type LibraryPage} from './upload-library';
import {folderWord} from '../lib/design-folder-intent';

export type DesignFile = {id: string; name: string; folder_id?: string | null};
const endpoint = '/api/agent-builder/';
const normalize = (value: string) => value.normalize('NFC').toLowerCase().replace(/\s+/g, ' ').trim();

function namedFolder(text: string, folders: LibraryFolder[]) {
  const normalized = normalize(text).replace(/\s*\/\s*/g, '/');
  const marker = folderWord.exec(normalized);
  if (!marker || folderWord.test(normalized.slice(marker.index + marker[0].length))) return;
  const before = normalized.slice(0,marker.index).trimEnd().replace(/[”’"'」]$/, '');
  const after = normalized.slice(marker.index + marker[0].length)
    .replace(/^\s*(?:이름(?:은|이)?|명은|는|은|[:=])?\s*[“‘"'「]?/, '');
  const matches = folders.flatMap(folder => {
    const path = folderTrail(folder.id, folders).map(item => normalize(item.name)).join('/');
    const name = [path, normalize(folder.name)].find(candidate => {
      const boundary = /[\p{L}\p{N}_/-]/u;
      return before.endsWith(candidate) && !boundary.test(before.at(-candidate.length - 1) || '') ||
        after.startsWith(candidate) && !boundary.test(after[candidate.length] || '');
    });
    return name ? [{folder, length: name.length}] : [];
  });
  const longestLength = matches.reduce((max, item) => Math.max(max, item.length), 0);
  const longest = matches.filter(match => match.length === longestLength);
  return longest.length === 1 ? longest[0].folder : undefined;
}

export async function resolveDesignFolder(text: string, connected: DesignFile[], mode:'all'|'pick') {
  const {folders} = await request<LibraryPage>(endpoint + 'library');
  const folder = namedFolder(text, folders);
  if (!folder) return {notice: '읽을 폴더를 경로로 선택하세요.'};
  if (mode==='pick') return {folder,notice:'이 폴더에서 사용할 파일을 선택하세요.'};
  const page = await request<LibraryPage>(endpoint + 'library?folder_id=' + encodeURIComponent(folder.id));
  const files = page.assets.filter(item => !item.pending_delete).map(({id, name}) => ({id, name}));
  if (!files.length && !page.next) return {folder, notice: '이 폴더에 연결할 파일이 없습니다.'};
  if (page.next || new Set([...connected, ...files].map(file => file.id)).size > 10)
    return {folder, notice: '설계에 연결할 수 있는 자료는 최대 10개입니다. 사용할 파일을 선택하세요.'};
  return {folder, files};
}

export type FilePickerRequest = {
  hint: string; contextHint?: string; initialFolder?: string; initialFiles?: DesignFile[]; notice?: string;
  apply: (files: DesignFile[], folder:{id:string|null;path:string}) => void;
};

export function DesignFolderPicker({picker, connected, close}: {
  picker: FilePickerRequest; connected: DesignFile[]; close: () => void;
}) {
  const firstFolder = picker.initialFiles?.[0]?.folder_id ?? picker.initialFolder;
  const [folderId, setFolderId] = useState(firstFolder || '');
  const [files, setFiles] = useState<DesignFile[]>(picker.initialFiles || []);
  const suggested = useRef(firstFolder !== undefined || !!picker.initialFiles?.length);
  const {folders, items, next, loading, error, load} = useLibrary(endpoint, folderId || null, '');
  useEffect(() => {
    if (suggested.current || loading || error) return;
    suggested.current = true;
    const folder = namedFolder(picker.hint, folders) || namedFolder(picker.contextHint || '', folders);
    if (folder) setFolderId(folder.id);
  }, [folders, loading, error, picker.hint, picker.contextHint]);
  const available = items.filter(file => !file.pending_delete);
  const selected = new Set(files.map(file => file.id));
  const used = new Set([...connected, ...files].map(file => file.id));
  const fits = new Set([...used, ...available.map(file => file.id)]).size <= 10;
  return <Dialog open onOpenChange={open => {if (!open) close();}}>
    <DialogContent className="as-dialog"><DialogHeader><DialogTitle>자료함에서 파일 선택</DialogTitle>
      <DialogDescription>{picker.notice || '내 자료함에서 이번 설계에 사용할 파일을 선택하세요.'}</DialogDescription></DialogHeader>
      <label>자료 폴더<select className="as-input" value={folderId} onChange={event => {
        suggested.current = true; setFolderId(event.target.value); setFiles([]);
      }}><option value="">내 자료함 · 최상위</option>{folders.map(folder =>
        <option key={folder.id} value={folder.id}>{folderTrail(folder.id, folders).map(item => item.name).join(' / ')}</option>)}</select></label>
      {error && <p role="alert">{error} <button type="button" onClick={() => void load(next)}>다시 시도</button></p>}
      {loading && <p role="status">파일을 불러오는 중입니다.</p>}
      {!loading && !error && !available.length && <p>이 폴더에는 선택할 파일이 없습니다.</p>}
      <div className="design-folder-files">{available.map(({id, name}) => <label key={id}>
        <input type="checkbox" checked={selected.has(id)} disabled={loading || (!used.has(id) && used.size >= 10)}
          onChange={event => setFiles(event.target.checked ? [...files, {id, name, folder_id:folderId || null}] : files.filter(file => file.id !== id))}/>{name}
      </label>)}</div>
      {next && !error && <button type="button" className="as-text-button" disabled={loading} onClick={() => void load(next)}>파일 더 보기</button>}
      {!fits && <p>현재 연결된 자료를 포함해 최대 10개까지 선택할 수 있습니다.</p>}
      <div className="as-run-actions"><button type="button" className="as-button" disabled={loading || !!error || !!next || !fits || !available.length}
        onClick={() => setFiles(available.map(({id, name}) => ({id, name, folder_id:folderId || null})))}>이 폴더 전체 선택</button>
      <button type="button" className="as-button as-primary" disabled={loading || !!error || !files.length}
        onClick={() => picker.apply(files,{id:folderId || null,path:['내 자료함',...folderTrail(folderId || null,folders).map(item=>item.name)].join(' / ')})}>{files.length}개 파일 사용</button></div>
    </DialogContent>
  </Dialog>;
}

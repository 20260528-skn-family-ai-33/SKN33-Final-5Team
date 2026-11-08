import {useRef, useState, type FormEvent} from 'react';
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '@/components/ui/dialog';

export function WorkflowRenameDialog({name, onSave, onClose}: {
  name: string;
  onSave: (name: string) => Promise<void>;
  onClose: () => void;
}) {
  const [value, setValue] = useState(name);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const trimmed = value.trim();
  async function save(event: FormEvent) {
    event.preventDefault();
    if (inFlight.current) return;
    if (!trimmed || trimmed.length > 60 || /[\u0000-\u001f\u007f]/.test(trimmed)) {
      setError('워크플로우 이름을 한 줄로 1~60자 입력해주세요.');
      return;
    }
    if (trimmed === name) { onClose(); return; }
    inFlight.current = true;
    setBusy(true);
    setError('');
    try { await onSave(trimmed); onClose(); }
    catch (err) { setError((err as Error).message); }
    finally { inFlight.current = false; setBusy(false); }
  }
  return <Dialog open onOpenChange={open => { if (!open && !inFlight.current) onClose(); }}>
    <DialogContent className="as-dialog" showCloseButton={!busy}>
      <DialogHeader><DialogTitle>워크플로우 이름 변경</DialogTitle>
        <DialogDescription>목록에서 알아보기 쉬운 이름을 입력하세요.</DialogDescription></DialogHeader>
      <form className="workflow-rename-form" onSubmit={event => void save(event)} aria-busy={busy}>
        <label htmlFor="workflow-rename-name">워크플로우 이름</label>
        <input id="workflow-rename-name" value={value} maxLength={60} disabled={busy}
          aria-describedby={error ? 'workflow-rename-error' : undefined} aria-invalid={!!error}
          onChange={event => { setValue(event.target.value); setError(''); }}/>
        {error && <p id="workflow-rename-error" role="alert">{error}</p>}
        <div><button type="button" className="as-button" disabled={busy} onClick={onClose}>취소</button>
          <button type="submit" className="as-button as-primary" disabled={busy || !trimmed}>{busy ? '저장 중…' : '이름 저장'}</button></div>
      </form>
    </DialogContent>
  </Dialog>;
}

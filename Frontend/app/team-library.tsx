import {useCallback, useEffect, useRef, useState, type FormEvent} from 'react';
import {Bell, Plus, RefreshCw, Settings, Users} from 'lucide-react';
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '@/components/ui/dialog';
import {request} from '../components/flow/connection-panel';
import {useAuth} from './auth';
import './team-library.css';

export type TeamRole = 'admin' | 'editor' | 'viewer';
export type TeamSummary = {id: string; name: string; role: TeamRole; created_at?: string};
export type TeamInvitation = {id: string; team_id: string; team_name: string; email: string; role: TeamRole; status?: string;
  expires_at?: string; inviter?: {id: string; name: string; email: string}};
type TeamMember = {id: string; user_id: string; email: string; name: string; role: TeamRole};
export const teamRoleLabels: Record<TeamRole, string> = {admin: '관리자', editor: '편집자', viewer: '열람자'};
export function teamLibraryBase(teamId?: string | null) {
  return teamId ? '/api/teams/' + encodeURIComponent(teamId) + '/library/' : '/api/agent-builder/';
}

export function useTeamSpaces(enabled: boolean) {
  const [teams, setTeams] = useState<TeamSummary[]>([]), [invitations, setInvitations] = useState<TeamInvitation[]>([]);
  const [loading, setLoading] = useState(enabled), [error, setError] = useState('');
  const generation = useRef(0);
  const reload = useCallback(async () => {
    const current = ++generation.current;
    if (!enabled) {setTeams([]); setInvitations([]); setLoading(false); setError(''); return;}
    setLoading(true); setError('');
    const results = await Promise.allSettled([
      request<{teams: TeamSummary[]}>('/api/teams'), request<{invitations: TeamInvitation[]}>('/api/team-invitations'),
    ]);
    if (current !== generation.current) return;
    const [teamResult, invitationResult] = results;
    if (teamResult.status === 'fulfilled') setTeams(teamResult.value.teams);
    if (invitationResult.status === 'fulfilled') setInvitations(invitationResult.value.invitations);
    setError(results.flatMap(result => result.status === 'rejected' ? [(result.reason as Error).message] : []).join(' '));
    setLoading(false);
  }, [enabled]);
  useEffect(() => {void reload(); return () => {generation.current++;};}, [reload]);
  return {teams, invitations, loading, error, reload};
}

function RoleOptions() {return <>{(['admin', 'editor', 'viewer'] as TeamRole[]).map(role => <option key={role} value={role}>{teamRoleLabels[role]}</option>)}</>;}

function TeamManager({team, close, onChanged}: {team: TeamSummary; close: () => void; onChanged: () => Promise<void>}) {
  const [members, setMembers] = useState<TeamMember[]>([]), [invitations, setInvitations] = useState<TeamInvitation[]>([]);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [email, setEmail] = useState(''), [role, setRole] = useState<TeamRole>('editor');
  const generation = useRef(0), writing = useRef(false), mounted = useRef(true);
  const endpoint = '/api/teams/' + encodeURIComponent(team.id);
  const load = useCallback(async () => {
    const current = ++generation.current; setLoading(true);
    try {
      const result = await request<{members: TeamMember[]; invitations: TeamInvitation[]}>(endpoint + '/members');
      if (current === generation.current) {setMembers(result.members); setInvitations(result.invitations);}
    } catch (e) {if (current === generation.current) setError((e as Error).message);}
    finally {if (current === generation.current) setLoading(false);}
  }, [endpoint]);
  useEffect(() => {mounted.current = true; void load(); return () => {mounted.current = false; generation.current++;};}, [load]);
  async function mutate(path: string, body: unknown, method: string, success: string) {
    if (writing.current || loading) return;
    writing.current = true; setBusy(true); setError(''); setNotice('');
    try {
      await request(endpoint + path, body, method);
      if (!mounted.current) return;
      setNotice(success); if (path === '/invitations') setEmail('');
      await load(); await onChanged();
    } catch (e) {if (mounted.current) setError((e as Error).message);}
    finally {writing.current = false; if (mounted.current) setBusy(false);}
  }
  return <Dialog open onOpenChange={open => {if (!open && !busy) close();}}><DialogContent className="as-dialog team-manager" showCloseButton={!busy}>
    <DialogHeader><DialogTitle>{team.name} 팀 관리</DialogTitle><DialogDescription>관리자는 팀원을 관리하고, 편집자는 자료를 정리하며, 열람자는 자료를 읽고 내려받을 수 있습니다.</DialogDescription></DialogHeader>
    {error && <p role="alert">{error} <button type="button" className="as-text-button" disabled={busy || loading} onClick={() => {setError(''); void load();}}>다시 불러오기</button></p>}
    {notice && <p role="status">{notice}</p>}
    {loading && <p role="status">팀원을 불러오는 중입니다.</p>}
    <form className="team-invite-form" onSubmit={event => {event.preventDefault(); void mutate('/invitations', {email: email.trim(), role}, 'POST', '서비스 안에서 확인할 수 있는 초대를 만들었습니다.');}}>
      <h3>이메일로 팀원 초대</h3><p>이메일은 발송하지 않습니다. 초대한 주소로 로그인하면 받은 초대에 표시됩니다.</p>
      <label>초대할 이메일<input type="email" required maxLength={254} value={email} disabled={busy || loading} onChange={event => setEmail(event.target.value)}/></label>
      <label>초대 권한<select value={role} disabled={busy || loading} onChange={event => setRole(event.target.value as TeamRole)}><RoleOptions/></select></label>
      <button type="submit" className="as-button as-primary" disabled={busy || loading || !email.trim()}>팀 초대 만들기</button>
    </form>
    <section aria-label="팀 구성원"><h3>구성원 {members.length}명</h3><ul className="team-member-list">{members.map(member => <li key={member.id}>
      <span><strong>{member.name || member.email}</strong><small>{member.email}</small></span>
      <select aria-label={member.email + ' 권한'} value={member.role} disabled={busy || loading} onChange={event => void mutate('/members/' + encodeURIComponent(member.user_id || member.id), {role: event.target.value}, 'PATCH', '팀원 권한을 변경했습니다.')}><RoleOptions/></select>
      <button type="button" className="as-text-button" aria-label={member.email + ' 팀에서 제외'} disabled={busy || loading} onClick={() => void mutate('/members/' + encodeURIComponent(member.user_id || member.id), undefined, 'DELETE', '팀원을 제외했습니다.')}>제외</button>
    </li>)}</ul></section>
    {!!invitations.length && <section aria-label="보낸 팀 초대"><h3>수락 대기 중</h3><ul className="team-member-list">{invitations.map(invitation => <li key={invitation.id}>
      <span><strong>{invitation.email}</strong><small>{teamRoleLabels[invitation.role]}{invitation.expires_at ? ' · ' + new Date(invitation.expires_at).toLocaleDateString('ko-KR') + '까지' : ''}</small></span>
      <button type="button" className="as-text-button" aria-label={invitation.email + ' 초대 취소'} disabled={busy || loading} onClick={() => void mutate('/invitations/' + encodeURIComponent(invitation.id), undefined, 'DELETE', '초대를 취소했습니다.')}>초대 취소</button>
    </li>)}</ul></section>}
    <div className="team-dialog-footer"><button type="button" className="as-button" disabled={busy} onClick={close}>닫기</button></div>
  </DialogContent></Dialog>;
}

export function TeamLibraryControls({selected, onSelect, disabled = false}: {selected: TeamSummary | null; onSelect: (team: TeamSummary | null) => void; disabled?: boolean}) {
  const auth = useAuth(), enabled = !!(auth.enabled && auth.user && !auth.loading);
  const {teams, invitations, loading, error, reload} = useTeamSpaces(enabled);
  const [dialog, setDialog] = useState<'create' | 'invitations' | 'manage' | null>(null);
  const [name, setName] = useState(''), [busy, setBusy] = useState(false), [actionError, setActionError] = useState(''), [notice, setNotice] = useState('');
  const writing = useRef(false), mounted = useRef(true);
  useEffect(() => {mounted.current = true; return () => {mounted.current = false;};}, []);
  useEffect(() => {
    if (!selected || loading || error || disabled) return;
    const current = teams.find(team => team.id === selected.id);
    if (!current) onSelect(null);
    else if (current.role !== selected.role || current.name !== selected.name) onSelect(current);
  }, [selected, teams, loading, error, disabled, onSelect]);
  function open(value: 'create' | 'invitations' | 'manage') {setActionError(''); setName(''); setDialog(value);}
  async function mutate(path: string, body: unknown, success: string, selectCreated = false) {
    if (writing.current) return;
    writing.current = true; setBusy(true); setActionError(''); setNotice('');
    try {
      const result = await request<{team?: TeamSummary}>(path, body);
      if (!mounted.current) return;
      await reload();
      if (!mounted.current) return;
      setNotice(success);
      if (selectCreated && result.team) {onSelect(result.team); setDialog(null);}
    } catch (e) {if (mounted.current) setActionError((e as Error).message);}
    finally {writing.current = false; if (mounted.current) setBusy(false);}
  }
  if (!enabled) return <div className="team-library-signin"><Users size={16} aria-hidden="true"/><p>팀 자료함은 로그인 후 사용할 수 있습니다.</p></div>;
  return <section className="team-library-spaces" aria-label="자료 공간">
    <div className="team-library-space-toolbar"><label>자료 공간<select aria-label="자료 공간 선택" value={selected?.id || ''} disabled={disabled || busy || loading} onChange={event => onSelect(teams.find(team => team.id === event.target.value) || null)}>
      <option value="">내 자료 · 개인</option>{teams.map(team => <option value={team.id} key={team.id}>{team.name} · {teamRoleLabels[team.role]}</option>)}
    </select></label><div className="team-library-space-actions">
      <button type="button" className="as-button" disabled={disabled || busy || loading} onClick={() => open('create')}><Plus size={14}/>팀 만들기</button>
      <button type="button" className="as-button" disabled={disabled || busy || loading} onClick={() => open('invitations')}><Bell size={14}/>받은 초대 {invitations.length}</button>
      {selected?.role === 'admin' && <button type="button" className="as-button" disabled={disabled || busy || loading} onClick={() => open('manage')}><Settings size={14}/>팀 관리</button>}
      <button type="button" className="as-button" aria-label="팀 공간 새로고침" disabled={disabled || busy || loading} onClick={() => void reload()}><RefreshCw size={14}/></button>
    </div></div>
    {selected && <p className="team-library-role"><Users size={14} aria-hidden="true"/>{selected.name} · {teamRoleLabels[selected.role]}{selected.role === 'viewer' ? ' · 자료를 읽고 내려받을 수 있습니다.' : ' · 팀 자료를 올리고 정리할 수 있습니다.'}</p>}
    {loading && <p role="status">팀 공간을 불러오는 중입니다.</p>}{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {dialog === 'manage' && selected?.role === 'admin' && <TeamManager key={selected.id} team={selected} close={() => setDialog(null)} onChanged={reload}/>}
    {(dialog === 'create' || dialog === 'invitations') && <Dialog open onOpenChange={open => {if (!open && !busy) setDialog(null);}}><DialogContent className="as-dialog team-space-dialog" showCloseButton={!busy}>
      <DialogHeader><DialogTitle>{dialog === 'create' ? '팀 만들기' : '받은 팀 초대'}</DialogTitle><DialogDescription>{dialog === 'create' ? '팀 자료를 함께 관리할 공간을 만듭니다. 만든 사람은 관리자가 됩니다.' : '현재 로그인한 이메일로 받은 초대입니다. 수락하면 팀 자료함을 사용할 수 있습니다.'}</DialogDescription></DialogHeader>
      {actionError && <p role="alert">{actionError}</p>}
      {dialog === 'create' ? <form onSubmit={(event: FormEvent) => {event.preventDefault(); void mutate('/api/teams', {name: name.trim()}, '팀을 만들었습니다.', true);}}>
        <label>팀 이름<input autoFocus required maxLength={80} value={name} disabled={busy} onChange={event => setName(event.target.value)}/></label>
        <div className="team-dialog-footer"><button type="button" className="as-button" disabled={busy} onClick={() => setDialog(null)}>취소</button><button type="submit" className="as-button as-primary" disabled={busy || !name.trim()}>{busy ? '만드는 중…' : '팀 생성'}</button></div>
      </form> : <><ul className="team-invitation-list">{invitations.map(invitation => <li key={invitation.id}>
        <div><strong>{invitation.team_name}</strong><span>{teamRoleLabels[invitation.role]} · {invitation.email}</span>{invitation.inviter && <small>초대한 사람: {invitation.inviter.name || invitation.inviter.email}</small>}</div>
        <button type="button" className="as-button as-primary" disabled={busy} aria-label={invitation.team_name + ' 초대 수락'} onClick={() => void mutate('/api/team-invitations/' + encodeURIComponent(invitation.id) + '/accept', {}, '팀 초대를 수락했습니다.', true)}>수락</button>
        <button type="button" className="as-button" disabled={busy} aria-label={invitation.team_name + ' 초대 거절'} onClick={() => void mutate('/api/team-invitations/' + encodeURIComponent(invitation.id) + '/decline', {}, '팀 초대를 거절했습니다.')}>거절</button>
      </li>)}</ul>{!invitations.length && <p>받은 초대가 없습니다.</p>}<div className="team-dialog-footer"><button type="button" className="as-button" disabled={busy} onClick={() => setDialog(null)}>닫기</button></div></>}
    </DialogContent></Dialog>}
  </section>;
}

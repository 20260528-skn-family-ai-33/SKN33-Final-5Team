"use client";

import {useState,useEffect} from 'react';
import {ErrorAgent} from './error-agent';
import type {CredentialPublic} from '@/server/contracts';
import {DataView} from './data-view';
import {History, CheckCircle2, AlertCircle, RefreshCw, ChevronRight, Clock3, CirclePause, CircleX, Loader2, Pin, X} from 'lucide-react';
import {toast} from 'sonner';
import type {Execution} from '@/lib/workflow';
import {Table, TableHeader, TableBody, TableRow, TableHead, TableCell} from '@/components/ui/table';
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '@/components/ui/select';
import {Tabs, TabsContent, TabsList, TabsTrigger} from '@/components/ui/tabs';

const statuses = {
  success: {label: '성공', Icon: CheckCircle2, color: 'var(--a4b-success)'},
  error: {label: '오류', Icon: AlertCircle, color: 'var(--a4b-danger)'},
  queued: {label: '시작 대기', Icon: Clock3, color: 'var(--a4b-blue-ink)'},
  running: {label: '실행 중', Icon: Loader2, color: 'var(--a4b-warning)'},
  waiting: {label: '기다리는 중', Icon: CirclePause, color: 'var(--a4b-blue-ink)'},
  canceled: {label: '중지됨', Icon: CircleX, color: 'var(--a4b-muted)'},
};

function Status({execution}: {execution: Execution}) {
  const {label, Icon, color} = statuses[execution.status];
  return <span className={`execution-status ${execution.status}`} style={{color}}><Icon size={15} className={execution.status === 'running' ? 'animate-spin' : ''}/>{label}</span>;
}

function formatDuration(milliseconds: number) {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return '—';
  if (milliseconds < 1000) return `${Math.round(milliseconds)}ms`;
  if (milliseconds < 60000) return `${(milliseconds / 1000).toFixed(1)}초`;
  return `${Math.floor(milliseconds / 60000)}분 ${Math.round(milliseconds % 60000 / 1000)}초`;
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('ko-KR', {timeZone: 'Asia/Seoul', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit'});
}

export function ExecutionView({executions, onReload, workflowId, onCancel, onRetry, initialExecutionId, credentials=[], onConfigure}: {
  credentials?:CredentialPublic[];
  onConfigure?:()=>void;
  initialExecutionId?: string;
  executions: Execution[];
  onReload: () => void | Promise<void>;
  workflowId?: string;
  onCancel?: (id: string) => void | Promise<void>;
  onRetry?: (id: string) => void | Promise<void>;
}) {
  const [filter, setFilter] = useState('all');
  const [modeFilter, setModeFilter] = useState('all');
  const [selectedId, setSelectedId] = useState<string | null>(initialExecutionId||null);
  useEffect(()=>{if(initialExecutionId){setSelectedId(initialExecutionId);setFilter('all');setModeFilter('all');}},[initialExecutionId]);
  const [busy, setBusy] = useState('');
  const selected = executions.find(execution => execution.id === selectedId && (!workflowId || execution.workflowId === workflowId));
  const workflowExecutions = executions.filter(execution => !workflowId || execution.workflowId === workflowId);
  const rows = workflowExecutions.filter(execution => (filter === 'all' || execution.status === filter) && (modeFilter === 'all' || execution.mode === modeFilter));

  async function perform(action: string, callback: () => void | Promise<void>) {
    if (busy) return;
    setBusy(action);
    try { await callback(); }
    catch (err) { toast.error((err as Error).message || '작업을 완료하지 못했어요. 다시 시도해주세요.'); }
    finally { setBusy(''); }
  }

  return <div className="panel-content">
    <div className="panel-title">
      <div><h1>실행 기록</h1><p>실행 결과와 각 단계에서 받은 정보, 만든 결과를 확인하세요.</p></div>
      <div className="flex gap-2 flex-wrap">
        <Select value={modeFilter} onValueChange={setModeFilter}><SelectTrigger className="w-32" aria-label="실행 방식 필터"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">모든 실행</SelectItem><SelectItem value="learning">학습 실행</SelectItem><SelectItem value="live">실제 실행</SelectItem></SelectContent></Select>
        <Select value={filter} onValueChange={setFilter}><SelectTrigger className="w-32" aria-label="실행 결과 필터"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">전체 결과</SelectItem>{Object.entries(statuses).map(([value, status]) => <SelectItem key={value} value={value}>{status.label}</SelectItem>)}</SelectContent></Select>
        <button className="btn" disabled={!!busy} onClick={() => void perform('reload', onReload)}><RefreshCw className={busy === 'reload' ? 'animate-spin' : ''}/>새로고침</button>
      </div>
    </div>
    <div className="help-box mb-5"><b>학습 실행과 실제 실행의 차이</b>학습 실행은 데이터 정리·조건 확인과 API 데이터 읽기를 연습해요. AI 호출, 메시지 전송과 데이터 변경은 실제 실행에서 수행해요.</div>
    <div className="execution-layout">
      <section className="execution-table" aria-label="실행 목록">
        {rows.length > 0 ? <Table>
          <TableHeader><TableRow><TableHead>결과</TableHead><TableHead>워크플로</TableHead><TableHead>시작 시간 (한국)</TableHead><TableHead>소요 시간</TableHead><TableHead><span className="sr-only">상세 보기</span></TableHead></TableRow></TableHeader>
          <TableBody>{rows.map(execution => <TableRow key={execution.id} className={selected?.id === execution.id ? 'selected-row' : ''} onClick={() => setSelectedId(execution.id)}>
            <TableCell><Status execution={execution}/>{execution.runs.some(run => run.continued) && <small className="block mt-1 text-[var(--a4b-warning)]">오류 후 계속한 단계 있음</small>}{execution.runs.some(run=>run.warning)&&<small className="block mt-1 text-[var(--a4b-warning)]">결과 안내 있음</small>}</TableCell>
            <TableCell>{execution.workflowName}<small className="block muted">{execution.mode === 'live' ? '실제 실행' : '학습 실행'}</small></TableCell>
            <TableCell>{formatDate(execution.startedAt)}</TableCell>
            <TableCell>{execution.status === 'queued' ? '—' : formatDuration(execution.duration)}</TableCell>
            <TableCell><button aria-label={`${execution.workflowName} 실행 상세`} className="icon-btn" onClick={event => {event.stopPropagation(); setSelectedId(execution.id);}}><ChevronRight/></button></TableCell>
          </TableRow>)}</TableBody>
        </Table> : <div className="empty-state"><History size={38}/><h3>{workflowExecutions.length ? '조건에 맞는 실행 기록이 없어요' : '아직 실행한 기록이 없어요'}</h3><p>{workflowExecutions.length ? '실행 방식이나 결과 필터를 바꿔보세요.' : '편집기에서 학습 실행이나 실제 실행을 누르면 결과가 여기에 저장돼요.'}</p>{workflowExecutions.length > 0 && <button className="btn" onClick={() => {setFilter('all'); setModeFilter('all');}}>전체 기록 보기</button>}</div>}
      </section>
      {selected && <aside className="execution-detail" aria-label="선택한 실행 상세">
        <div className="flex items-start justify-between gap-2 mb-4"><div><h2 className="mb-2!">실행 상세</h2><Status execution={selected}/></div><button className="icon-btn" aria-label="실행 상세 닫기" onClick={() => setSelectedId(null)}><X size={17}/></button></div>
        <p className="text-sm mb-3">{selected.workflowName}</p>
        <p className="muted text-xs mb-4">{selected.mode === 'live' ? '실제 실행' : '학습 실행'} · {formatDate(selected.startedAt)} · {formatDuration(selected.duration)}</p>
        {selected.runs.some(run => run.continued) && <div className="help-box mb-4">오류가 발생한 단계가 있지만, 설정에 따라 다음 단계로 계속 실행했어요. 각 단계에서 오류 내용을 확인하세요.</div>}
        {(selected.status==='error'||selected.runs.some(r=>r.error))&&<ErrorAgent key={selected.id} execution={selected} credentials={credentials} onConfigure={onConfigure}/>}
        {selected.error && <div className="error-box mb-4" role="alert">{selected.error}</div>}
        {selected.status === 'queued' && <p className="muted text-sm mb-4">실행 순서를 기다리고 있어요. 시작되면 단계별 결과가 표시돼요.</p>}
        {selected.status === 'running' && <p className="muted text-sm mb-4" role="status">단계를 실행하고 있어요. 새로고침으로 진행 상황을 확인하세요.</p>}
        {selected.status === 'waiting' && <p className="muted text-sm mb-4">설정한 대기 시간이 지나면 다음 단계로 이어져요.</p>}
        {selected.status === 'canceled' && <p className="muted text-sm mb-4">실행을 중지했어요. 완료된 단계의 결과는 아래에서 확인할 수 있어요.</p>}
        <div className="flex gap-2 mb-4 flex-wrap">
          {onCancel && ['queued', 'running', 'waiting'].includes(selected.status) && <button className="btn" disabled={!!busy} onClick={() => void perform('cancel', () => onCancel(selected.id))}>{busy === 'cancel' ? <Loader2 className="animate-spin"/> : <CircleX/>}실행 중지</button>}
          {onRetry && ['error', 'canceled'].includes(selected.status) && <button className="btn primary" disabled={!!busy} onClick={() => void perform('retry', () => onRetry(selected.id))}>{busy === 'retry' ? <Loader2 className="animate-spin"/> : <RefreshCw/>}같은 설정으로 다시 실행</button>}
        </div>
        {selected.runs.length === 0 && !['queued', 'running'].includes(selected.status) && <p className="muted text-sm">저장된 단계 결과가 없어요.</p>}
        {selected.runs.map((run, index) => <details className="run-detail" key={`${selected.id}-${run.nodeId}-${index}`} open={run.status === 'error' || !!run.warning || undefined}>
          <summary><span className={`run-state ${run.status}`}/><span className="min-w-0 flex-1"><span className="block">{run.name}{selected.runs.filter(r=>r.nodeId===run.nodeId).length>1&&<small className="muted ml-2">{(run.runIndex??selected.runs.slice(0,index).filter(r=>r.nodeId===run.nodeId).length)+1}번째 처리</small>}</span>{(run.continued || (run.attempts || 0) > 1 || run.pinned) && <span className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1 text-xs">{run.continued && <span className="text-[var(--a4b-warning)]">오류 후 계속</span>}{(run.attempts || 0) > 1 && <span className="inline-flex items-center gap-1 text-[var(--a4b-blue-ink)]"><RefreshCw size={11}/>{run.attempts}회 시도</span>}{run.pinned && <span className="inline-flex items-center gap-1 text-[var(--a4b-blue-ink)]"><Pin size={11}/>고정 데이터</span>}</span>}</span><small className="shrink-0">{run.status === 'skipped' ? '건너뜀' : `${run.output.flat().length}개 · ${formatDuration(run.duration)}`}</small></summary>
          {run.pinned && <p className="muted text-xs mt-3">이 단계는 실행 대신 미리 고정한 데이터를 다음 단계로 전달했어요.</p>}
          {run.warning&&<div className="help-box mt-3" role="status">{run.warning}</div>}
          {run.pagination&&<p className="muted text-xs mt-3">{run.pagination.pagesFetched}페이지 · {run.pagination.itemsFetched}개 항목{run.pagination.limited?' · 지정한 페이지까지만 처리':run.pagination.complete?' · 마지막 페이지까지 처리':''}</p>}
          {run.error && <p className="error-text mt-3">{run.error}</p>}
          {run.status === 'skipped' ? <p className="muted text-xs mt-3">이 경로로 넘어온 데이터가 없거나 단계가 꺼져 있어 건너뛰었어요.</p> : <Tabs defaultValue="output" className="mt-3">
            <TabsList className="config-tabs"><TabsTrigger value="output">만든 결과</TabsTrigger><TabsTrigger value="input">받은 정보</TabsTrigger></TabsList>
            <TabsContent value="output">{run.output.map((items,output)=><div key={output}>{run.output.length>1&&<b className="output-label">출력 {output+1}</b>}<DataView items={items} executionId={selected.id} runIndex={index} output={output}/></div>)}</TabsContent>
            <TabsContent value="input"><DataView items={run.input} executionId={selected.id} runIndex={index} side="input"/></TabsContent>
          </Tabs>}
        </details>)}
      </aside>}
    </div>
  </div>;
}

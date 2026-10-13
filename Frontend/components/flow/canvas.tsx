"use client";

import {useState, useRef, useEffect, type PointerEvent, type KeyboardEvent} from 'react';
import {Plus, Play, BookOpen, Sparkles, X, ZoomOut, ZoomIn, Maximize, Undo2, Redo2, Check, AlertCircle, Loader2, Settings2, Copy, Power, Trash2} from 'lucide-react';
import {ContextMenu, ContextMenuTrigger, ContextMenuContent, ContextMenuItem, ContextMenuLabel, ContextMenuSeparator, ContextMenuShortcut} from '@/components/ui/context-menu';
import {type Flow, type FlowNode, type Execution, clone, nodeInfo, nodePorts, deleteNode} from '@/lib/workflow';
import {nodeVisuals} from './node-picker';
import {StickyNote, isStickyNote, stickyNoteSize, stickyNoteColor} from './sticky-note';
import {toast} from 'sonner';

type NodeDrag = {kind: 'node'; id: string; x: number; y: number; position: [number, number]; moved: boolean; flow: Flow; next?: Flow};
type PanDrag = {kind: 'pan'; x: number; y: number; position: [number, number]};
type NoteResize = {kind: 'resize'; id: string; x: number; y: number; width: number; height: number; flow: Flow; next?: Flow};

function squareHeight(node: FlowNode) {
  const ports = nodePorts(node);
  return Math.max(88, 32 + (Math.max(ports.inputs, ports.outputs.length) - 1) * 24);
}

function portCenter(index: number, count: number, height: number) {
  return count <= 1 ? height / 2 : (index + 1) * height / (count + 1);
}

function outputColor(node: FlowNode, output: number) {
  const ports = nodePorts(node);
  if (node.onError === 'continueErrorOutput' && output === ports.outputs.length - 1) return '#f97373';
  if (node.type === 'n8n-nodes-base.if') return output === 0 ? '#7fb685' : '#d89494';
  return '#b3b3b3';
}

export function Canvas({flow, onChange, onSelect, onAdd, onRun, running, execution, beginner, guide, setGuide, onUndo, onRedo, canUndo, canRedo}: {
  flow: Flow; onChange: (flow: Flow) => void; onSelect: (node: FlowNode) => void; onAdd: () => void; onRun: () => void;
  running: boolean; execution: Execution | null; beginner: boolean; guide: boolean; setGuide: (value: boolean) => void;
  onUndo: () => void; onRedo: () => void; canUndo: boolean; canRedo: boolean;
}) {
  const [step, setStep] = useState(0);
  const [view, setView] = useState({x: 0, y: 0, scale: 1});
  const [draft, setDraft] = useState<Flow | null>(null);
  const [connecting, setConnecting] = useState<{name: string; output: number} | null>(null);
  const [focused, setFocused] = useState<string | null>(null);
  const [editingNote, setEditingNote] = useState<string | null>(null);
  const canvas = useRef<HTMLDivElement>(null);
  const drag = useRef<NodeDrag | PanDrag | NoteResize | null>(null);
  const displayed = draft || flow;
  const steps = displayed.nodes.filter(node => !isStickyNote(node));
  const runCounts=new Map<string,number>();for(const record of execution?.runs||[])runCounts.set(record.nodeId,(runCounts.get(record.nodeId)||0)+1);

  function fit() {
    const rect = canvas.current?.getBoundingClientRect();
    if (!rect || !flow.nodes.length) return;
    const minX = Math.min(...flow.nodes.map(node => node.position[0])) - 60;
    const maxX = Math.max(...flow.nodes.map(node => node.position[0] + (isStickyNote(node) ? stickyNoteSize(node).width : 150))) + 60;
    const minY = Math.min(...flow.nodes.map(node => node.position[1])) - 65;
    const maxY = Math.max(...flow.nodes.map(node => node.position[1] + (isStickyNote(node) ? stickyNoteSize(node).height : squareHeight(node)))) + 90;
    const availableHeight = Math.max(100, rect.height - 150);
    const scale = Math.min(1, Math.max(.25, Math.min((rect.width - 48) / (maxX - minX), availableHeight / (maxY - minY))));
    setView({x: (rect.width - (maxX - minX) * scale) / 2 - minX * scale, y: 80 + (availableHeight - (maxY - minY) * scale) / 2 - minY * scale, scale});
  }

  const fitRef=useRef(fit);fitRef.current=fit;
  useEffect(()=>{if(!canvas.current)return;const observer=new ResizeObserver(()=>{if(!drag.current)fitRef.current();});observer.observe(canvas.current);return()=>observer.disconnect();},[]);
  useEffect(()=>{fitRef.current();},[flow.nodes.length]);
  useEffect(() => {fit(); setConnecting(null); setDraft(null); setFocused(null); setEditingNote(null); drag.current = null;}, [flow.id]);
  useEffect(() => {
    if (connecting && !flow.nodes.some(node => node.name === connecting.name && connecting.output < nodePorts(node).outputs.length)) setConnecting(null);
    if (focused && !flow.nodes.some(node => node.id === focused)) setFocused(null);
    if (editingNote && !flow.nodes.some(node => node.id === editingNote)) setEditingNote(null);
  }, [flow, connecting, focused]);

  function startNode(event: PointerEvent<HTMLDivElement>, node: FlowNode) {
    if (event.button !== 0 || (event.target as HTMLElement).closest('button,input,textarea,select,a')) return;
    event.stopPropagation();
    event.currentTarget.focus();
    event.currentTarget.setPointerCapture(event.pointerId);
    setFocused(node.id);
    drag.current = {kind: 'node', id: node.id, x: event.clientX, y: event.clientY, position: node.position, moved: false, flow: clone(flow)};
  }

  function pointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!event.currentTarget.contains(event.target as Node)) return;
    const data = drag.current;
    if (!data) return;
    const dx = event.clientX - data.x, dy = event.clientY - data.y;
    if (data.kind === 'node') {
      if (Math.abs(dx) + Math.abs(dy) <= 5 && !data.moved) return;
      data.moved = true;
      const next = clone(data.flow), node = next.nodes.find(item => item.id === data.id);
      if (!node) return;
      node.position = [Math.round(data.position[0] + dx / view.scale), Math.round(data.position[1] + dy / view.scale)];
      data.next = next;
      setDraft(next);
    } else if (data.kind === 'resize') {
      const next = clone(data.flow), node = next.nodes.find(item => item.id === data.id);
      if (!node) return;
      node.parameters.width = Math.min(3000, Math.max(240, Math.round(data.width + dx / view.scale)));
      node.parameters.height = Math.min(3000, Math.max(160, Math.round(data.height + dy / view.scale)));
      data.next = next;
      setDraft(next);
    } else setView(previous => ({...previous, x: data.position[0] + dx, y: data.position[1] + dy}));
  }

  function endNode(node: FlowNode) {
    const data = drag.current;
    if (!data || data.kind === 'pan') return;
    drag.current = null;
    if (data.kind === 'resize') {if (data.next) onChange(data.next);}
    else if (data.moved && data.next) onChange(data.next);
    else if (!isStickyNote(node)) onSelect(node);
    setDraft(null);
  }

  function removeNode(node: FlowNode) {
    onChange(deleteNode(flow, node.id));
    if (connecting?.name === node.name) setConnecting(null);
    setFocused(null);
    if (editingNote === node.id) setEditingNote(null);
    toast(isStickyNote(node) ? '메모를 삭제했어요. 되돌리기로 복구할 수 있어요.' : '단계를 삭제했어요. 되돌리기로 복구할 수 있어요.');
  }

  function duplicateNode(node: FlowNode) {
    const next = clone(flow);
    let name = node.name + ' 복사', suffix = 2;
    while (next.nodes.some(item => item.name === name)) name = node.name + ' 복사 ' + suffix++;
    const isTrigger = nodeInfo(node)?.category === '시작';
    const copy: FlowNode = {...clone(node), id: crypto.randomUUID(), name, position: [node.position[0] + 52, node.position[1] + (isStickyNote(node) ? stickyNoteSize(node).height : squareHeight(node)) + 65], disabled: isTrigger || !!node.disabled};
    next.nodes.push(copy);
    if (!isStickyNote(node) && next.connections[node.name]) next.connections[name] = clone(next.connections[node.name]);
    onChange(next);
    toast(isStickyNote(node) ? '메모를 복제했어요.' : isTrigger ? '시작 단계 복사본은 꺼진 상태예요. 사용할 시작 단계는 하나만 켜주세요.' : '단계를 복제했어요. 입력 연결점을 이어서 사용하세요.');
  }

  function toggleNode(node: FlowNode) {
    const next = clone(flow), target = next.nodes.find(item => item.id === node.id);
    if (!target) return;
    target.disabled = !target.disabled;
    onChange(next);
    toast(target.disabled ? '이 단계를 껐어요. 받은 정보를 그대로 다음 단계에 보내요.' : '이 단계를 켰어요.');
  }

  function connectInput(node: FlowNode, input: number) {
    if (!connecting) {toast('먼저 앞 단계의 오른쪽 연결점을 선택해주세요.'); return;}
    if (connecting.name === node.name) {toast.error('같은 단계에 연결할 수 없어요.'); return;}
    const visited = new Set<string>();
    function reaches(name: string): boolean {
      if (name === connecting!.name) return true;
      if (visited.has(name)) return false;
      visited.add(name);
      return (flow.connections[name]?.main || []).some(edges => edges.some(edge => reaches(edge.node)));
    }
    if (reaches(node.name)) {
      const bodyVisited=new Set<string>();
      function bodyReaches(name:string):boolean {if(name===connecting!.name)return true;if(name===node.name||bodyVisited.has(name))return false;bodyVisited.add(name);return (flow.connections[name]?.main||[]).some(edges=>edges.some(edge=>bodyReaches(edge.node)));}
      const loopFeedback=node.type==='n8n-nodes-base.splitInBatches'&&(flow.connections[node.name]?.main?.[1]||[]).some(edge=>bodyReaches(edge.node));
      if(!loopFeedback){toast.error('반복 경로의 마지막 단계에서 반복 단계 입력으로 돌아오는 연결만 만들 수 있어요.');return;}
    }
    const next = clone(flow);
    next.connections[connecting.name] ??= {main: []};
    next.connections[connecting.name].main ??= [];
    while (next.connections[connecting.name].main.length <= connecting.output) next.connections[connecting.name].main.push([]);
    const edges = next.connections[connecting.name].main[connecting.output];
    if (!edges.some(edge => edge.node === node.name && edge.index === input)) edges.push({node: node.name, type: 'main', index: input});
    onChange(next);
    setConnecting(null);
    toast.success('두 단계를 연결했어요.');
  }

  function nodeKey(event: KeyboardEvent<HTMLDivElement>, node: FlowNode) {
    if (event.currentTarget !== event.target) return;
    if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); event.stopPropagation(); if (isStickyNote(node)) startEditNote(node); else onSelect(node);}
    if (event.key === 'Delete' || event.key === 'Backspace') {event.preventDefault(); event.stopPropagation(); removeNode(node);}
    if (event.key === 'Escape') {event.preventDefault(); setConnecting(null);}
  }

  function startEditNote(node: FlowNode) {
    setFocused(node.id); setEditingNote(node.id);
    const rect = canvas.current?.getBoundingClientRect();
    if (rect) setView({x: Math.max(16, (rect.width - Math.max(320, stickyNoteSize(node).width)) / 2) - node.position[0], y: 76 - node.position[1], scale: 1});
  }

  function saveNote(node: FlowNode, parameters: FlowNode['parameters']) {
    const next = clone(flow), target = next.nodes.find(item => item.id === node.id);
    if (!target) return;
    target.parameters = {...target.parameters, ...parameters};
    onChange(next); setEditingNote(null);
  }

  function startResize(event: PointerEvent<HTMLButtonElement>, node: FlowNode) {
    if (event.button !== 0) return;
    event.preventDefault(); event.stopPropagation(); event.currentTarget.setPointerCapture(event.pointerId);
    const size = stickyNoteSize(node);
    setFocused(node.id);
    drag.current = {kind: 'resize', id: node.id, x: event.clientX, y: event.clientY, ...size, flow: clone(flow)};
  }

  function resizeKey(event: KeyboardEvent<HTMLButtonElement>, node: FlowNode) {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    const size = stickyNoteSize(node), amount = event.shiftKey ? 100 : 20;
    saveNote(node, {width: Math.min(3000, Math.max(240, size.width + (event.key === 'ArrowLeft' ? -amount : event.key === 'ArrowRight' ? amount : 0))), height: Math.min(3000, Math.max(160, size.height + (event.key === 'ArrowUp' ? -amount : event.key === 'ArrowDown' ? amount : 0)))});
  }

  const last = steps[steps.length - 1];

  return <div ref={canvas} className="canvas"
    onPointerDown={event => {
      if (!event.currentTarget.contains(event.target as Node)) return;
      if (event.button !== 0 || (event.target as HTMLElement).closest('.node,.sticky-note,.btn,.guide,.canvas-tools,.mini-map,.add-node-end,.line-click')) return;
      setFocused(null);
      drag.current = {kind: 'pan', x: event.clientX, y: event.clientY, position: [view.x, view.y]};
      event.currentTarget.setPointerCapture(event.pointerId);
    }}
    onPointerMove={pointerMove}
    onPointerUp={event => {if (!event.currentTarget.contains(event.target as Node)) return; if (drag.current?.kind === 'pan') drag.current = null;}}
    onPointerCancel={event => {if (!event.currentTarget.contains(event.target as Node)) return; drag.current = null; setDraft(null);}}
    onWheel={event => {if (event.ctrlKey || event.metaKey) setView(previous => ({...previous, scale: Math.max(.25, Math.min(1.8, previous.scale + (event.deltaY < 0 ? .08 : -.08)))}));}}
  >
    <div className="canvas-heading"><span>WORKFLOW DRAFT</span><strong>{connecting ? '다음 단계의 왼쪽 연결점을 눌러주세요.' : '반복되는 일, 한 번만 만들어두세요.'}</strong></div>
    <div className="canvas-toptools">{connecting && <button className="btn" onClick={() => setConnecting(null)}>연결 취소</button>}<button className="btn" onClick={onAdd}><Plus/>단계 추가</button></div>
    <div className="canvas-content" style={{transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`}}>
      {beginner && guide && <div className="canvas-note"><b><BookOpen/>내 첫 번째 워크플로</b>왼쪽에서 오른쪽으로 실행돼요.<br/>단계를 눌러 설정하고, 연결점을 눌러 이어주세요.<br/>오른쪽 클릭으로 복제·끄기·삭제할 수 있어요.</div>}
      {displayed.nodes.filter(isStickyNote).map(node => <ContextMenu key={node.id}>
        <ContextMenuTrigger asChild><div className="sticky-note-context"><StickyNote node={node} selected={focused === node.id} editing={editingNote === node.id} onStartEdit={() => startEditNote(node)} onSave={parameters => saveNote(node, parameters)} onCancel={() => setEditingNote(null)} onPointerDown={event => startNode(event, node)} onPointerUp={() => endNode(node)} onResizeStart={event => startResize(event, node)} onResizeKey={event => resizeKey(event, node)} onKeyDown={event => nodeKey(event, node)} onFocus={() => setFocused(node.id)} onContextMenu={() => {setFocused(node.id); drag.current = null; setDraft(null);}}/></div></ContextMenuTrigger>
        <ContextMenuContent className="min-w-44"><ContextMenuLabel>{node.name}</ContextMenuLabel><ContextMenuSeparator/><ContextMenuItem onSelect={() => startEditNote(node)}><Settings2/>메모 편집<ContextMenuShortcut>Enter</ContextMenuShortcut></ContextMenuItem><ContextMenuItem onSelect={() => onSelect(node)}><Settings2/>메모 설정</ContextMenuItem><ContextMenuItem onSelect={() => duplicateNode(node)}><Copy/>메모 복제</ContextMenuItem><ContextMenuSeparator/><ContextMenuItem variant="destructive" onSelect={() => removeNode(node)}><Trash2/>메모 삭제<ContextMenuShortcut>Delete</ContextMenuShortcut></ContextMenuItem></ContextMenuContent>
      </ContextMenu>)}
      <svg className="connections" width="4000" height="2500" aria-label="작업 단계 연결">
        {Object.entries(displayed.connections).flatMap(([source, channels]) => Object.entries(channels).flatMap(([channel, outputs]) => outputs.flatMap((edges, output) => edges.map((edge, edgeIndex) => {
          const from = displayed.nodes.find(node => node.name === source), to = displayed.nodes.find(node => node.name === edge.node);
          if (!from || !to || isStickyNote(from) || isStickyNote(to)) return null;
          const fromPorts = nodePorts(from), toPorts = nodePorts(to);
          const sx = from.position[0] + 115, sy = from.position[1] + portCenter(output, Math.max(1, fromPorts.outputs.length), squareHeight(from));
          const tx = to.position[0] + 27, ty = to.position[1] + portCenter(edge.index, Math.max(1, toPorts.inputs), squareHeight(to));
          const bend = Math.max(50, Math.abs(tx - sx) * .5), path = `M${sx} ${sy} C${sx + bend} ${sy} ${tx - bend} ${ty} ${tx} ${ty}`;
          const edgeUsed=execution?.runs.some(record=>record.nodeId===from.id&&record.status==='success'&&record.output[output]?.length), color = outputColor(from, output);
          const remove = () => {const next = clone(flow); next.connections[source][channel][output].splice(edgeIndex, 1); onChange(next); toast('연결을 지웠어요. 되돌리기로 복구할 수 있어요.');};
          return <g key={`${source}-${channel}-${output}-${edgeIndex}`}>
            <path d={path} style={{stroke: edgeUsed ? '#74c391' : color}} strokeDasharray={channel === 'main' ? undefined : '5 5'}/>
            <path className="line-click" d={path} role="button" tabIndex={0} aria-label={`${source}에서 ${edge.node}로 이어진 연결 삭제`} onClick={event => {event.stopPropagation(); remove();}} onKeyDown={event => {if (['Enter', ' ', 'Delete', 'Backspace'].includes(event.key)) {event.preventDefault(); event.stopPropagation(); remove();}}}/>
          </g>;
        }))))}
      </svg>
      {steps.map((node, index) => {
        const info = nodeInfo(node), visual = nodeVisuals[info?.id || 'set'] || nodeVisuals.set, Icon = visual.Icon;
        const run = execution?.runs.findLast(record => record.nodeId === node.id), trigger = info?.category === '시작';
        const ports = nodePorts(node), height = squareHeight(node);
        return <ContextMenu key={node.id}>
          <ContextMenuTrigger asChild><div role="button" tabIndex={0} aria-label={`단계 ${index + 1} ${node.name} 설정. Enter로 열기, Delete로 삭제`} className={`node ${trigger ? 'trigger' : ''} ${run?.status || ''} ${node.disabled ? 'disabled-node' : ''} ${focused === node.id ? 'selected' : ''}`} style={{left: node.position[0], top: node.position[1]}}
            onFocus={() => setFocused(node.id)} onContextMenu={() => {setFocused(node.id); drag.current = null; setDraft(null);}}
            onPointerDown={event => startNode(event, node)} onPointerUp={event => {if (!(event.target as HTMLElement).closest('button')) endNode(node);}} onKeyDown={event => nodeKey(event, node)}
          >
            <span className="node-step">{beginner ? `STEP ${String(index + 1).padStart(2, '0')}` : info?.englishName || node.type.split('.').pop()}</span>
            <div className="node-square" style={{color: visual.color, height}}>
              {Array.from({length: ports.inputs}, (_, input) => <button key={input} className="port in" style={{top: portCenter(input, ports.inputs, height) - 6}} aria-label={`${node.name} 입력 ${input + 1} 연결`} title={`입력 ${input + 1}`} onPointerDown={event => event.stopPropagation()} onClick={() => connectInput(node, input)}/>)}
              <Icon/>
              {ports.outputs.map((label, output) => <span key={output} style={{display: 'contents'}}>
                <button className={`port out ${connecting?.name === node.name && connecting.output === output ? 'selected-port' : ''}`} style={{top: portCenter(output, ports.outputs.length, height) - 6, background: connecting?.name === node.name && connecting.output === output ? 'var(--primary)' : outputColor(node, output)}} aria-label={`${node.name} ${label} 연결`} title={label} onPointerDown={event => event.stopPropagation()} onClick={() => setConnecting({name: node.name, output})}/>
                {ports.outputs.length > 1 && <span style={{position: 'absolute', top: portCenter(output, ports.outputs.length, height) - 8, left: 'calc(100% + 12px)', color: outputColor(node, output), fontSize: 11, maxWidth: 105, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', pointerEvents: 'none'}}>{label}</span>}
              </span>)}
              {run?.status === 'success' && <span className="node-status"><Check/></span>}
              {run?.status === 'error' && <span className="node-status error-icon"><AlertCircle/></span>}
            </div>
            <div className="node-name">{node.name}</div>
            <div className="node-description">{node.disabled ? '꺼진 단계 · 데이터만 전달' : run ? run.continued ? '오류 후 계속' : run.pinned ? '고정 데이터 사용' : run.status === 'skipped' ? '입력 없음 · 건너뜀' : run.status === 'error' ? '설정을 확인해주세요' : `${(runCounts.get(node.id)||0)>1?runCounts.get(node.id)+'회 처리 · 마지막 ':''}${run.output.flat().length}개 항목 · 실행 완료` : info?.englishName || '가져온 단계'}</div>
          </div></ContextMenuTrigger>
          <ContextMenuContent className="min-w-44">
            <ContextMenuLabel>{node.name}</ContextMenuLabel><ContextMenuSeparator/>
            <ContextMenuItem onSelect={() => onSelect(node)}><Settings2/>설정 열기<ContextMenuShortcut>Enter</ContextMenuShortcut></ContextMenuItem>
            <ContextMenuItem onSelect={() => duplicateNode(node)}><Copy/>단계 복제</ContextMenuItem>
            <ContextMenuItem onSelect={() => toggleNode(node)}><Power/>{node.disabled ? '단계 켜기' : '단계 끄기'}</ContextMenuItem>
            <ContextMenuSeparator/><ContextMenuItem variant="destructive" onSelect={() => removeNode(node)}><Trash2/>단계 삭제<ContextMenuShortcut>Delete</ContextMenuShortcut></ContextMenuItem>
          </ContextMenuContent>
        </ContextMenu>;
      })}
      {last && <button className="add-node-end" style={{left: last.position[0] + 193, top: last.position[1] + squareHeight(last) / 2 - 20}} aria-label="새 단계 추가" onClick={onAdd}><Plus size={19}/></button>}
      {!steps.length && <div className="canvas-empty"><Plus size={32}/><h2>첫 번째 단계를 추가해보세요</h2><p>언제 시작할지 고르면 자동화 만들기가 시작돼요.</p><button className="btn primary" onClick={onAdd}>시작 단계 선택</button></div>}
    </div>
    {guide && beginner && <aside className="guide"><div className="guide-head"><b><Sparkles/>함께 만들어볼까요?</b><button className="icon-btn" onClick={() => setGuide(false)} aria-label="가이드 닫기"><X/></button></div><div className="guide-content"><div className="guide-steps">{[0, 1, 2].map(index => <span key={index} className={index <= step ? 'current' : ''}/>)}</div><h3>{['자동화는 작은 단계의 연결이에요', '단계를 눌러 설정을 바꿔요', '이제 한 번 실행해보세요'][step]}</h3><p>{['언제 시작할지, 어떤 일을 할지, 결과를 어디에 보낼지. 순서대로 연결하면 워크플로가 완성돼요.', '각 네모가 하나의 작업이에요. 데이터와 조건을 바꾸면 나에게 필요한 자동화가 돼요.', '아래 ‘시험 실행’을 누르면 각 단계를 거치는 데이터와 결과를 확인할 수 있어요.'][step]}</p><div className="guide-footer"><span>{step + 1} / 3</span><button className="btn" onClick={() => step < 2 ? setStep(step + 1) : setGuide(false)}>{step < 2 ? '다음' : '시작하기'}</button></div></div></aside>}
    <div className="canvas-tools"><button className="icon-btn" aria-label="축소" onClick={() => setView(previous => ({...previous, scale: Math.max(.25, previous.scale - .1)}))}><ZoomOut/></button><span className="zoom-label">{Math.round(view.scale * 100)}%</span><button className="icon-btn" aria-label="확대" onClick={() => setView(previous => ({...previous, scale: Math.min(1.8, previous.scale + .1)}))}><ZoomIn/></button><div className="tool-divider"/><button className="icon-btn" aria-label="전체 보기" onClick={fit}><Maximize/></button><div className="tool-divider"/><button className="icon-btn" aria-label="되돌리기" onClick={onUndo} disabled={!canUndo}><Undo2/></button><button className="icon-btn" aria-label="다시 하기" onClick={onRedo} disabled={!canRedo}><Redo2/></button></div>
    <div className="execute-controls"><button className="btn primary" disabled={running} onClick={onRun}>{running ? <Loader2 className="animate-spin"/> : <Play fill="currentColor"/>}{running ? '실행 중…' : '실행 준비'}</button></div>
    <button className="mini-map" aria-label="워크플로 전체 보기" onClick={fit}><svg viewBox="0 0 1100 520">{flow.nodes.map(node => <rect key={node.id} x={node.position[0]} y={node.position[1] - 100} width={isStickyNote(node) ? stickyNoteSize(node).width : 88} height={isStickyNote(node) ? stickyNoteSize(node).height : squareHeight(node)} rx={isStickyNote(node) ? 2 : 7} fill={isStickyNote(node) ? `var(--sticky-${stickyNoteColor(node)}-background)` : execution?.runs.findLast(record => record.nodeId === node.id)?.status === 'success' ? '#71af88' : '#737373'}/>)}</svg></button>
  </div>;
}

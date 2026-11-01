"use client";

import {useEffect, useState, type ReactNode, type PointerEvent, type KeyboardEvent} from 'react';
import {Check, Grip, Pencil, StickyNote as NoteIcon, X} from 'lucide-react';
import type {FlowNode, Json} from '@/lib/workflow';

export const stickyNoteType = 'n8n-nodes-base.stickyNote';
export const isStickyNote = (node: FlowNode) => node.type === stickyNoteType;

export function stickyNoteSize(node: FlowNode) {
  const dimension = (value: unknown, fallback: number, minimum: number) => typeof value === 'number' && Number.isFinite(value) ? Math.min(3000, Math.max(minimum, value)) : fallback;
  return {width: dimension(node.parameters.width, 240, 240), height: dimension(node.parameters.height, 160, 160)};
}

export function stickyNoteColor(node: FlowNode) {
  const color = Number(node.parameters.color);
  return Number.isInteger(color) && color >= 1 && color <= 7 ? color : 1;
}

const colorNames = ['노랑', '갈색', '빨강', '초록', '파랑', '보라', '회색'];

// React escapes every text fragment. No HTML injection or image requests are used.
function inlineMarkdown(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const pattern = /!\[[^\]]*\]\([^)]*\)|\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
  let offset = 0, match: RegExpExecArray | null;
  while ((match = pattern.exec(text))) {
    if (match.index > offset) parts.push(text.slice(offset, match.index));
    const key = match.index;
    if (match[0].startsWith('!')) parts.push(match[0]);
    else if (match[1]) parts.push(<strong key={key}>{match[1]}</strong>);
    else if (match[2]) parts.push(<code key={key}>{match[2]}</code>);
    else parts.push(<a key={key} href={match[4]} target="_blank" rel="noopener noreferrer" onPointerDown={event => event.stopPropagation()}>{match[3]}</a>);
    offset = pattern.lastIndex;
  }
  if (offset < text.length) parts.push(text.slice(offset));
  return parts;
}

function NoteMarkdown({content}: {content: string}) {
  return <div className="sticky-note-markdown">{content.split('\n').map((line, index) => {
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      const Tag = heading[1].length === 1 ? 'h2' : heading[1].length === 2 ? 'h3' : 'h4';
      return <Tag key={index}>{inlineMarkdown(heading[2])}</Tag>;
    }
    const item = /^\s*(?:[-*]|\d+\.)\s+(.+)$/.exec(line);
    if (item) return <div className="sticky-note-list-item" key={index}><span aria-hidden="true">•</span><span>{inlineMarkdown(item[1])}</span></div>;
    return <p key={index}>{line ? inlineMarkdown(line) : <br/>}</p>;
  })}</div>;
}

export function StickyNote({node, selected, editing, onStartEdit, onSave, onCancel, onPointerDown, onPointerUp, onResizeStart, onResizeKey, onKeyDown, onFocus, onContextMenu}: {
  node: FlowNode; selected: boolean; editing: boolean;
  onStartEdit: () => void; onSave: (parameters: Json) => void; onCancel: () => void;
  onPointerDown: (event: PointerEvent<HTMLDivElement>) => void; onPointerUp: () => void;
  onResizeStart: (event: PointerEvent<HTMLButtonElement>) => void; onResizeKey: (event: KeyboardEvent<HTMLButtonElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void; onFocus: () => void; onContextMenu: () => void;
}) {
  const size = stickyNoteSize(node), color = stickyNoteColor(node);
  const content = typeof node.parameters.content === 'string' ? node.parameters.content : '';
  const [text, setText] = useState(content);
  const [draftColor, setDraftColor] = useState(color);
  const [width, setWidth] = useState(String(size.width));
  const [height, setHeight] = useState(String(size.height));
  useEffect(() => {
    if (!editing) return;
    setText(content); setDraftColor(color); setWidth(String(size.width)); setHeight(String(size.height));
  }, [editing]);

  const validSize = Number.isFinite(Number(width)) && Number(width) >= 240 && Number(width) <= 3000 && Number.isFinite(Number(height)) && Number(height) >= 160 && Number(height) <= 3000;
  const validContent = text.length <= 20000;
  const save = () => {if (validSize && validContent) onSave({content: text, color: draftColor, width: Math.round(Number(width)), height: Math.round(Number(height))});};

  return <div role="group" tabIndex={0} aria-label={`${node.name} 메모. Enter로 편집, Delete로 삭제`} className={`sticky-note sticky-note-${editing ? draftColor : color}${selected ? ' selected' : ''}${editing ? ' editing' : ''}`} style={{left: node.position[0], top: node.position[1], width: size.width, height: size.height}}
    onFocus={onFocus} onContextMenu={onContextMenu} onKeyDown={onKeyDown}
  >
    <div className="sticky-note-drag" onPointerDown={editing ? undefined : onPointerDown} onPointerUp={editing ? undefined : onPointerUp}>
      <span><NoteIcon size={14}/>{node.name}</span>
      {!editing && <button className="icon-btn" onClick={onStartEdit} aria-label={`${node.name} 내용 편집`} title="메모 편집"><Pencil/></button>}
    </div>
    {!editing && <div className="sticky-note-body" onDoubleClick={onStartEdit}><NoteMarkdown content={content || '두 번 눌러 설명을 적어보세요.'}/></div>}
    {!editing && <button className="sticky-note-resize" aria-label={`${node.name} 크기 조절. 방향키로 20픽셀씩 변경`} title="드래그 또는 방향키로 크기 조절" onPointerDown={onResizeStart} onPointerUp={onPointerUp} onKeyDown={onResizeKey}><Grip size={14}/></button>}
    {editing && <div className="sticky-note-editor" onPointerDown={event => event.stopPropagation()} onKeyDown={event => {event.stopPropagation(); if (event.key === 'Escape') {event.preventDefault(); onCancel();} if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {event.preventDefault(); save();}}}>
      <label className="sr-only" htmlFor={`note-content-${node.id}`}>메모 내용</label>
      <textarea id={`note-content-${node.id}`} autoFocus value={text} onChange={event => setText(event.target.value)} placeholder="## 이 자동화의 목적\n단계에 대한 설명을 적어보세요."/>
      <div className="sticky-note-colors" role="group" aria-label="메모 색상">{colorNames.map((name, index) => <button key={name} className={`sticky-note-color sticky-note-${index + 1}`} aria-label={`${name} 메모`} aria-pressed={draftColor === index + 1} title={name} onClick={() => setDraftColor(index + 1)}>{draftColor === index + 1 && <Check size={13}/>}</button>)}</div>
      <div className="sticky-note-dimensions"><label>너비<input type="number" min={240} max={3000} step={20} value={width} onChange={event => setWidth(event.target.value)}/></label><label>높이<input type="number" min={160} max={3000} step={20} value={height} onChange={event => setHeight(event.target.value)}/></label><span>px</span></div>
      {!validContent && <p role="alert" className="sticky-note-error">메모 내용은 20,000자까지 적을 수 있어요.</p>}
      {!validSize && <p role="alert" className="sticky-note-error">너비 240~3000, 높이 160~3000으로 입력해주세요.</p>}
      <div className="sticky-note-edit-footer"><small>제목·목록·굵게·링크를 쓸 수 있어요.</small><button className="icon-btn" aria-label="메모 편집 취소" title="취소" onClick={onCancel}><X/></button><button className="btn" disabled={!validSize || !validContent} onClick={save}><Check/>적용</button></div>
    </div>}
  </div>;
}

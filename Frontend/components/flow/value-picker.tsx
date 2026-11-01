"use client";

import {useMemo, useState} from 'react';
import {Braces, List, MousePointer2} from 'lucide-react';
import {Popover, PopoverContent, PopoverTrigger} from '@/components/ui/popover';
import {Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList} from '@/components/ui/command';
import {buildValuePaths, type ValueSource, type ValuePath, type NodeValueSelection} from '@/lib/value-paths';

export type ValuePickerProps = {sources: ValueSource[]; onSelect: (expression: string, type?: ValuePath['type']) => void; disabled?: boolean};
const typeNames = {string: '글자', number: '숫자', boolean: '참/거짓', null: '빈 값', array: '목록', object: '객체'};

export function ValuePicker({sources, onSelect, disabled = false}: ValuePickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [selection, setSelection] = useState<NodeValueSelection>('linked');
  const data = useMemo(() => buildValuePaths(sources, {nodeSelection: selection}), [sources, selection]);
  const needle = query.trim().toLocaleLowerCase('ko-KR');
  const terms=needle.split(/\s+/).filter(Boolean);
  const matches=(value:string)=>terms.every(term=>value.toLocaleLowerCase('ko-KR').includes(term));
  const groups=data.groups.map(group=>({...group,showAll:!!group.allExpression&&matches(`${group.label} 전체 항목 목록`),values:group.values.filter(value=>matches(`${group.label} ${value.label} ${typeNames[value.type]} ${value.expression}`))})).filter(group=>group.values.length||group.showAll||(group.status!=='ready'||group.selectionUnavailable)&&(!needle||matches(group.label)));
  const hasData = data.groups.some(group => group.status === 'ready');
  const select = (expression: string, type: ValuePath['type']) => {onSelect(expression, type); setOpen(false); setQuery('');};

  return <Popover open={open} onOpenChange={value => {setOpen(value); if (!value) setQuery('');}}><PopoverTrigger asChild>
    <button type="button" className="btn ghost text-xs" disabled={disabled} aria-label="실행 결과에서 값 선택"><MousePointer2/>값 선택</button>
  </PopoverTrigger><PopoverContent align="start" className="w-[440px] max-w-[calc(100vw-24px)] p-0" onOpenAutoFocus={event => {if (disabled) event.preventDefault();}}>
    <div className="border-b px-4 py-3"><h3 className="flex items-center gap-2 text-sm font-medium"><Braces size={16}/>어떤 값을 사용할까요?</h3><p className="mt-2 text-xs leading-relaxed text-muted-foreground">예시는 <b className="font-medium text-foreground">첫 번째 입력 항목</b>으로 보여줘요. 실행할 때 현재 입력과 각 항목의 원본 값은 항목마다 달라져요.</p>
      {sources.some(source=>source.kind==='node')&&<div className="mt-3"><p className="mb-1.5 text-[11px] font-medium">이전 단계의 값을 가져오는 방법</p><div className="flex gap-1 rounded-md border p-1" role="group" aria-label="이전 단계 값 선택 방법"><button type="button" aria-pressed={selection==='linked'} onClick={()=>setSelection('linked')} className={`flex-1 rounded px-2 py-1.5 text-[11px] ${selection==='linked'?'bg-accent font-medium text-accent-foreground':'text-muted-foreground'}`}>각 항목의 원본 (추천)</button><button type="button" aria-pressed={selection==='first'} onClick={()=>setSelection('first')} className={`flex-1 rounded px-2 py-1.5 text-[11px] ${selection==='first'?'bg-accent font-medium text-accent-foreground':'text-muted-foreground'}`}>첫 항목</button></div><p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">{selection==='linked'?'고객 B에는 고객 B의 원본 값을 넣어요. 연결 기록이 확인된 결과만 사용할 수 있어요.':'첫 결과의 같은 값을 모든 항목에 넣어요. 예: 모든 고객에게 첫 고객의 이름을 넣어요.'}</p></div>}
    </div>
    <Command shouldFilter={false}><CommandInput placeholder="단계 이름이나 값 경로 검색" value={query} onValueChange={setQuery} aria-label="사용할 값 검색"/><CommandList className="max-h-[330px]">
      {!hasData && <CommandEmpty><p>아직 선택할 결과가 없어요.</p><p className="mt-2 px-5 text-xs leading-relaxed text-muted-foreground">먼저 시험 실행을 해보세요. 입력 데이터나 앞 단계의 결과가 여기에 나타나요.</p></CommandEmpty>}
      {hasData && !groups.some(group=>group.values.length||group.showAll||group.selectionUnavailable) && <CommandEmpty>찾는 값이 없어요. 다른 경로로 검색해보세요.</CommandEmpty>}
      {groups.map(group => <CommandGroup key={group.id} heading={`${group.label} · ${group.count}개 항목`}>
        {group.status === 'empty' && <p className="px-2 py-2 text-xs leading-relaxed text-muted-foreground">출력이 비어 있어요. 먼저 이 경로에 결과가 생기도록 실행해주세요.</p>}
        {group.status === 'invalid' && <p className="px-2 py-2 text-xs text-muted-foreground">단계 이름과 출력 번호를 확인해주세요.</p>}
        {group.status==='ready'&&group.selectionUnavailable&&<p className="px-2 py-2 text-xs leading-relaxed text-muted-foreground">{group.selectionUnavailable}</p>}
        {group.status==='ready'&&!group.selectionUnavailable&&group.selection==='linked'&&<p className="px-2 py-2 text-[11px] leading-relaxed text-muted-foreground">각 항목의 원본 · 아래는 첫 입력 항목에 연결된 원본 예시예요.</p>}
        {group.status==='ready'&&!group.selectionUnavailable&&group.selection==='first'&&<p className="px-2 py-2 text-[11px] leading-relaxed text-muted-foreground">첫 항목 · 선택한 값은 모든 입력 항목에 같게 들어가요.</p>}
        {group.showAll && <CommandItem value={`${group.id}-all`} onSelect={() => select(group.allExpression!, 'array')} className="items-start gap-2 py-2.5"><List className="mt-0.5 shrink-0"/><div className="min-w-0"><b className="block text-xs font-medium">전체 {group.count}개 항목 선택</b><span className="mt-1 block text-[11px] leading-relaxed text-muted-foreground">json을 담은 항목 목록이에요. .all()로 가져와요.</span><code className="mt-1 block break-all text-[11px] text-muted-foreground">{group.allExpression}</code></div></CommandItem>}
        {group.values.map(value => <CommandItem key={value.id} value={value.id} onSelect={() => select(value.expression, value.type)} className="items-start justify-between gap-3 py-2.5"><div className="min-w-0 flex-1"><span className="block break-all text-xs font-medium">{value.label}</span><span className="mt-1 block break-all text-xs text-muted-foreground">{value.preview}</span><code className="mt-1 block break-all text-[10px] text-muted-foreground">{value.expression}</code></div><span className="shrink-0 rounded border px-1.5 py-0.5 text-[10px] text-muted-foreground">{typeNames[value.type]}</span></CommandItem>)}
      </CommandGroup>)}
    </CommandList></Command>
    <div className="border-t px-4 py-2.5 text-[11px] leading-relaxed text-muted-foreground"><p>민감한 값과 파일 내부 정보는 숨겼어요. 전체 항목 선택은 json을 담은 목록을 넣어요.</p>{(data.truncated || data.depthLimited) && <p className="mt-1">최대 500개 경로, 8단계 깊이까지 보여줘요. 더 깊은 값은 고급 표현식으로 입력할 수 있어요.</p>}</div>
  </PopoverContent></Popover>;
}

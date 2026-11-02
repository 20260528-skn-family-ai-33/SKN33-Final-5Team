import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { Dialog as DialogPrimitive } from 'radix-ui';
import { X, ArrowLeft, ArrowRight } from 'lucide-react';
import { Dialog, DialogPortal, DialogOverlay, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import './product-tour.css';
export const TOUR_SEEN_KEY = 'a4b-product-tour-v1';
const NEXT_KEY = 'a4b-product-tour-next';
export type TourPanel = 'conversation' | 'canvas' | 'settings';
type Step = {
    target: string;
    title: string;
    text: string;
    panel?: TourPanel;
};
const common: Step[] = [
    { target: 'menu-workflows', title: '저장한 워크플로우를 관리하세요', text: '만들어 둔 업무 흐름을 모아 보는 곳입니다. 목록에서 설계를 열거나 바로 실행할 수 있습니다.' },
    { target: 'menu-history', title: '이전에 실행한 결과를 확인하세요', text: '언제 실행했는지, 완료됐는지 확인합니다. 실행 확인을 누르면 해당 실행의 결과와 처리 기록을 볼 수 있습니다.' },
    { target: 'menu-nodes', title: '자주 쓰는 작업은 준비해 두세요', text: '문서 읽기나 집계처럼 반복해서 사용할 작업을 저장하는 곳입니다. 처음에는 작업을 따로 만들지 않고 업무 설명부터 시작해도 됩니다.' },
    { target: 'create-workflow', title: '새 업무는 여기서 시작하세요', text: '새 워크플로우를 누르면 설계 화면이 열립니다. 어떤 자료를 어떻게 처리하고 싶은지 설명해 주세요.' },
];
export function tourSteps(scope: string): Step[] {
    if (scope === 'preview') return [{target:'create-workflow',title:'내 작업 공간으로 이동하세요',text:'내 워크플로우 열기를 누르면 실제 작업 목록으로 이동합니다. 지금 보고 있는 화면은 동작을 살펴보는 샘플이며 저장되거나 실행되지 않습니다.'}];
    if (scope === 'workflow')
        return [
            { target: 'conversation', panel: 'conversation', title: '할 일과 원하는 결과를 설명하세요', text: '예: “매출 파일에서 팀별 합계를 구해 JSON으로 저장해 줘.” 필요한 조건을 질문받으면 같은 대화에서 답합니다.' },
            { target: 'attach', panel: 'conversation', title: '필요한 파일을 첨부하세요', text: '업무에 사용할 표나 참고 문서를 연결합니다. 파일의 내용과 대화에서 정한 조건을 바탕으로 설계합니다.' },
            { target: 'canvas', panel: 'canvas', title: '어떤 순서로 처리하는지 확인하세요', text: '생성된 작업과 연결 순서가 표시됩니다. 작업을 누르면 입력 연결과 세부 설정을 확인할 수 있습니다. 흐름을 바꾸려면 대화로 요청하세요.' },
            { target: 'settings', panel: 'settings', title: '설정을 확인한 뒤 저장하세요', text: '이름, 지시사항, 사용할 도구와 자료를 확인합니다. 필요한 조건이 모두 정리되면 초안 확인 · 저장 버튼을 사용할 수 있습니다.' },
            { target: 'run-tab', title: '저장한 워크플로우를 실행하세요', text: '실행 화면에서 요청과 자료를 넣습니다. 결과가 나오면 근거와 처리 단계도 함께 확인하세요.' },
        ];
    if (scope === 'nodes')
        return [
            { target: 'preset-configure', title: '준비된 작업의 설정을 열어보세요', text: '설정하기를 누르면 이름과 처리 조건을 입력할 수 있습니다. 확인한 뒤 작업 저장을 눌러야 내 작업으로 저장됩니다.' },
            { target: 'preset-use', title: '이 작업을 쓰는 흐름을 시작하세요', text: '이 작업으로 설계를 누르면 선택한 작업을 사용하는 새 워크플로우를 시작합니다. 어떤 자료를 어떻게 처리할지 대화에서 설명하세요.' },
            { target: 'new-task', title: '직접 준비하려면 새 작업을 누르세요', text: 'AI가 할 일을 정하거나, 표 읽기·필터·집계 같은 데이터 처리 기능을 선택할 수 있습니다.' },
            { target: 'task-form', title: '이름과 처리 조건을 정하세요', text: 'AI 작업에는 목적과 지시사항을, 데이터 처리에는 필요한 열과 조건을 입력합니다. 작업 저장을 누르면 다시 사용할 수 있습니다.' },
            { target: 'saved-tasks', title: '저장한 작업을 다시 사용하세요', text: '설정 수정으로 새 버전을 저장하거나 워크플로우에서 사용을 누릅니다. 이미 저장한 흐름은 기존 작업 버전을 유지합니다.' },
        ];
    if (scope === 'run')
        return [
            { target: 'saved-workflows', title: '실행할 워크플로우를 선택하세요', text: '저장한 흐름을 선택하면 실행에 사용할 요청과 자료를 넣는 화면이 열립니다.' },
            { target: 'run-input', title: '이번에 처리할 요청을 입력하세요', text: '저장한 흐름에 전달할 내용입니다. 새 파일을 첨부하면 이번 실행에 사용합니다.' },
            { target: 'run-submit', title: '실행하고 결과를 확인하세요', text: '실행 버튼을 누르면 저장한 설정으로 처리합니다. 완료 후 결과, 근거와 단계 기록을 확인할 수 있습니다. 안내 중에는 실제 실행하지 않습니다.' },
            { target: 'run-result', title: '결과와 처리 기록을 살펴보세요', text: '표나 답변을 확인하고, 필요한 경우 원문 근거와 처리 단계를 펼쳐보세요. 다운로드 버튼이 있으면 결과 파일도 받을 수 있습니다.' },
        ];
    return common;
}
function read(key: string) {
    try {return localStorage.getItem(key);}
    catch {return 'done';}
}
function write(key: string, value: string) {
    try {localStorage.setItem(key, value);}
    catch {/* Guidance must remain usable without storage. */}
}
function remove(key: string) {
    try {localStorage.removeItem(key);}
    catch {/* Nothing to clear when storage is unavailable. */}
}
export function shouldStartTour(scope: string) { return !['intro', 'preview'].includes(scope) && (!read(TOUR_SEEN_KEY) || read(NEXT_KEY) === scope); }
const selector = (id: string) => `[data-tour="${id}"]`;
export function availableSteps(scope: string) {
    let steps = tourSteps(scope);
    if (scope === 'workflow' && document.querySelector(selector('edit-workflow'))) {
        steps = [
            {target:'conversation',panel:'conversation',title:'저장한 업무 내용을 확인하세요',text:'저장 당시의 목적과 요구 조건을 보여줍니다. 내용을 바꾸려면 설정 수정을 눌러 대화를 시작하세요.'},
            ...steps.filter(step => step.target === 'canvas'),
            {target:'settings',panel:'settings',title:'이 흐름이 사용하는 설정입니다',text:'저장된 이름, 지시사항, 도구와 자료를 확인하세요. 작업을 선택하면 해당 단계의 입력 연결과 규칙도 볼 수 있습니다.'},
            {target:'edit-workflow',title:'조건을 바꾸려면 설정 수정을 누르세요',text:'대화에서 바꿀 조건을 설명하고 확인한 내용을 새 버전으로 저장합니다. 안내 중에는 수정이나 저장을 실행하지 않습니다.'},
            ...steps.filter(step => step.target === 'run-tab'),
        ];
    }
    if (scope === 'run' && !document.querySelector('[data-tour="saved-workflows"] button')) {
        steps = [{target:'create-workflow',title:'먼저 워크플로우를 만들어 주세요',text:'실행하려면 저장한 워크플로우가 필요합니다. 새 워크플로우에서 업무를 설명하고, 처리 순서와 조건을 확인한 뒤 저장하세요.'}];
    }
    return steps.filter(step => document.querySelector(selector(step.target)));
}

type Rect = {
    top: number;
    left: number;
    width: number;
    height: number;
};
export function tourPosition(rect: Rect | null, width: number, height: number, cardHeight = 280) {
    const cardWidth = Math.min(340, Math.max(0, width - 32)), left = rect ? Math.max(16, Math.min(rect.left, width - cardWidth - 16)) : Math.max(16, (width - cardWidth) / 2);
    let top = rect ? rect.top + rect.height + 12 : Math.max(16, (height - cardHeight) / 2);
    if (top + cardHeight > height - 16)
        top = rect && rect.top - cardHeight - 12 >= 16 ? rect.top - cardHeight - 12 : height - cardHeight - 16;
    return { left, top: Math.max(16, top), width: cardWidth };
}
export function ProductTour({ scope, open, onClose, onStart, onPanelChange, currentPanel }: {
    scope: string;
    open: boolean;
    onClose: () => void;
    onStart?: () => void;
    onPanelChange?: (panel: TourPanel) => void;
    currentPanel?: TourPanel;
}) {
    const [steps, setSteps] = useState<Step[]>([]), [index, setIndex] = useState(0), [rect, setRect] = useState<Rect | null>(null), [viewport, setViewport] = useState({ width: 0, height: 0 }), [cardHeight, setCardHeight] = useState(280);
    const card = useRef<HTMLDivElement>(null), next = useRef<HTMLButtonElement>(null);
    const initialView = useRef<{scroll:number;panel?:TourPanel}>({scroll:0});
    const step = steps[index];
    // Capture the starting view once; the guide itself may switch mobile panels.
    useEffect(() => { if (open) {
        initialView.current={scroll:window.scrollY,panel:currentPanel};
        setSteps(availableSteps(scope));
        setIndex(0);
    }
    else
        setRect(null); }, [open, scope]);
    useEffect(() => { if (open && step?.panel)
        onPanelChange?.(step.panel); }, [open, step, onPanelChange]);
    useLayoutEffect(() => {
        if (!open || !step)
            return;
        let frame = 0, revealed = false;
        const element = document.querySelector<HTMLElement>(selector(step.target));
        element?.scrollIntoView?.({ block: 'nearest', behavior: 'instant' });
        const update = () => {
            cancelAnimationFrame(frame);
            frame = requestAnimationFrame(() => {
                setViewport({width: window.innerWidth, height: window.innerHeight});
                let bounds = element?.getBoundingClientRect();
                if (!revealed && bounds && bounds.width > 0 && bounds.height > 0) {
                    revealed = true;
                    if (bounds.top < 8 || bounds.bottom > window.innerHeight - 8) {
                        element?.scrollIntoView?.({block: 'nearest', behavior: 'instant'});
                        bounds = element?.getBoundingClientRect();
                    }
                }
                setRect(bounds && bounds.width > 0 && bounds.height > 0
                    ? {top: bounds.top - 5, left: bounds.left - 5, width: bounds.width + 10, height: bounds.height + 10}
                    : null);
                if (card.current) setCardHeight(card.current.getBoundingClientRect().height || 280);
            });
        };
        update();
        const observer = new ResizeObserver(update);
        if (element)
            observer.observe(element);
        if (card.current)
            observer.observe(card.current);
        window.addEventListener('resize', update);
        window.addEventListener('scroll', update, true);
        return () => { cancelAnimationFrame(frame); observer.disconnect(); window.removeEventListener('resize', update); window.removeEventListener('scroll', update, true); };
    }, [open, step]);
    const close = (restoreView = true) => {
        write(TOUR_SEEN_KEY, 'done');
        remove(NEXT_KEY);
        if(restoreView&&initialView.current.panel)onPanelChange?.(initialView.current.panel);
        onClose();
        if(restoreView)requestAnimationFrame(()=>window.scrollTo({top:initialView.current.scroll,behavior:'instant'}));
    };
    const finish = () => {
        const startDesign=scope==='workflows'&&!!onStart;
        close(!startDesign);
        if(startDesign){write(NEXT_KEY,'workflow');onStart?.();}
    };
    const position = tourPosition(rect, viewport.width || window.innerWidth, viewport.height || window.innerHeight, cardHeight);
    return <Dialog open={open && !!step} onOpenChange={value => {if (!value) close();}}>
        <DialogPortal>
            <DialogOverlay className="product-tour-overlay" style={{background: rect ? 'transparent' : 'rgba(18,31,46,.48)'}}/>
            {rect && <div aria-hidden="true" className="product-tour-spotlight"
                style={{top: rect.top, left: rect.left, width: rect.width, height: rect.height}}/>}
            <DialogPrimitive.Content ref={card} data-tour-target={step?.target} className="product-tour-card" style={position as CSSProperties}
                onOpenAutoFocus={event => {event.preventDefault(); next.current?.focus({preventScroll: true});}}
                onCloseAutoFocus={event => {
                    event.preventDefault();
                    document.querySelector<HTMLElement>('[data-tour="help"]')?.focus({preventScroll: true});
                }}
                onPointerDownOutside={event => event.preventDefault()}>
                <div className="product-tour-top">
                    <span>화면 둘러보기 · {index + 1} / {steps.length}</span>
                    <button type="button" onClick={()=>close()} aria-label="안내 닫기"><X size={16}/></button>
                </div>
                <DialogTitle className="product-tour-title">{step?.title}</DialogTitle>
                <DialogDescription className="product-tour-description">{step?.text}</DialogDescription>
                <div className="product-tour-progress" aria-hidden="true">
                    {steps.map((item, i) => <span key={item.target} className={i <= index ? 'done' : ''}/>)}
                </div>
                <div className="product-tour-actions">
                    <button type="button" className="as-text-button" onClick={()=>close()}>나중에 보기</button>
                    <button type="button" className="as-button" disabled={!index} onClick={() => setIndex(i => i - 1)}>
                        <ArrowLeft size={14}/>이전
                    </button>
                    <button type="button" className="as-button as-primary" ref={next}
                        onClick={() => index === steps.length - 1 ? finish() : setIndex(i => i + 1)}>
                        {index === steps.length - 1 ? (scope === 'workflows' ? '설계 시작' : '안내 마치기') : '다음'}
                        <ArrowRight size={14}/>
                    </button>
                </div>
            </DialogPrimitive.Content>
        </DialogPortal>
    </Dialog>;
}

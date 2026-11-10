import {ArrowRight, FileText, History, Mail, Plus, Table2, Workflow} from 'lucide-react';
import './workspace-overview.css';

type RecentWorkflow = {id:string; updated_at:string; config:{name:string; purpose:string}};
type RecentRun = {agent:RecentWorkflow; run:{id:string; status:string; started_at?:string; created_at?:string}};

const examples = [
  {title:'엑셀 정리하기', text:'재고표에서 기준 수량보다 재고가 적은 품목만 골라 엑셀로 정리해 줘.', icon:Table2},
  {title:'문서에서 항목 뽑기', text:'영수증 PDF에서 날짜, 가맹점, 결제 금액을 뽑아 표로 만들어 줘.', icon:FileText},
  {title:'문의 메일 요약하기', text:'이번 주에 받은 문의 메일을 주제별로 묶고 답변이 필요한 항목을 표시해 줘.', icon:Mail},
];
const runStatuses:Record<string,string> = {succeeded:'완료', failed:'실패', running:'실행 중', needs_input:'답변 대기'};
const timestamp = new Intl.DateTimeFormat('ko-KR', {dateStyle:'medium', timeStyle:'short', timeZone:'Asia/Seoul'});

function timeLabel(value?:string) {
  const time = Date.parse(value ?? '');
  return Number.isFinite(time) ? timestamp.format(time) : '';
}

export function WorkspaceOverview({workflows,runs,loading,error,historyPartial,onCreate,onExample,onNavigate,onRetry}:{
  workflows:RecentWorkflow[];
  runs:RecentRun[];
  loading:boolean;
  error:string;
  historyPartial:boolean;
  onCreate:()=>void;
  onExample:(prompt:string)=>void;
  onNavigate:(page:'workflows'|'workflow'|'history'|'run', service?:string, run?:string)=>void;
  onRetry:()=>void;
}) {
  return <div className="workspace-overview">
    <div className="entry-page-heading">
      <div><h1>홈</h1><p>새 워크플로우를 만들거나 최근 작업을 이어가세요.</p></div>
      <button type="button" className="as-button as-primary overview-create" data-tour="home-create" onClick={onCreate}>
        <Plus size={17} aria-hidden="true"/>새 워크플로우 만들기
      </button>
    </div>
    {error ? <div className="as-error" role="alert">
      <span>작업 공간을 불러오지 못했습니다. {error}</span><button type="button" onClick={onRetry}>다시 시도</button>
    </div> : loading ? <p className="entry-loading" role="status">최근 작업을 불러오고 있습니다.</p> :
    workflows.length === 0 ? <section aria-labelledby="home-examples">
      <h2 id="home-examples">어떤 업무부터 시작할까요?</h2>
      <p className="overview-description">예시를 선택하면 설계 화면에 업무 설명이 입력됩니다.</p>
      <div className="overview-examples">
        {examples.map(({title,text,icon:Icon})=><button type="button" key={title} onClick={()=>onExample(text)}>
          <span className="overview-icon"><Icon size={19} aria-hidden="true"/></span>
          <strong>{title}</strong><span className="overview-description">{text}</span>
          <span className="overview-more">이 예시로 시작 <ArrowRight size={14} aria-hidden="true"/></span>
        </button>)}
      </div>
    </section> : <div className="overview-columns">
      <section className="overview-panel" aria-labelledby="home-workflows">
        <div className="overview-panel-heading">
          <h2 id="home-workflows">최근 워크플로우</h2>
          <button type="button" className="as-text-button" onClick={()=>onNavigate('workflows')}>작업 라이브러리 <ArrowRight size={14} aria-hidden="true"/></button>
        </div>
        <ul>{workflows.map(workflow=><li key={workflow.id}>
          <button type="button" className="overview-row" onClick={()=>onNavigate('workflow',workflow.id)}>
            <span className="overview-icon"><Workflow size={18} aria-hidden="true"/></span>
            <span className="overview-item"><strong>{workflow.config.name}</strong><small>{timeLabel(workflow.updated_at)}</small></span>
            <ArrowRight size={15} aria-hidden="true"/>
          </button>
        </li>)}</ul>
      </section>
      <section className="overview-panel" aria-labelledby="home-runs">
        <div className="overview-panel-heading">
          <h2 id="home-runs">최근 실행 결과</h2>
          <button type="button" className="as-text-button" onClick={()=>onNavigate('history')}>실행 기록 <ArrowRight size={14} aria-hidden="true"/></button>
        </div>
        {historyPartial && <p className="overview-empty" role="status">일부 실행 기록을 불러오지 못했습니다.</p>}
        {runs.length > 0 ? <ul>{runs.map(({agent,run})=><li key={run.id}>
          <button type="button" className="overview-row" onClick={()=>onNavigate('run',agent.id,run.id)}>
            <span className="overview-icon"><History size={18} aria-hidden="true"/></span>
            <span className="overview-item"><strong>{agent.config.name}</strong><small>{timeLabel(run.started_at || run.created_at)}</small></span>
            <span className={'entry-run-status '+run.status}>{runStatuses[run.status] ?? run.status}</span>
            <ArrowRight size={15} aria-hidden="true"/>
          </button>
        </li>)}</ul> : !historyPartial && <p className="overview-empty">아직 실행 기록이 없습니다.</p>}
      </section>
    </div>}
  </div>;
}

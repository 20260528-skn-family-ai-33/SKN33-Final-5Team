import {useRef,useState} from 'react';
import {ArrowRight, ArrowUp, Paperclip, X, FileText, Layers, Table2, Workflow} from 'lucide-react';
import './workspace-overview.css';

type RecentWorkflow = {id:string; updated_at:string; config:{name:string}};
const examples = [
  {title:'엑셀 정리하기', text:'재고표에서 기준 수량보다 재고가 적은 품목만 골라 엑셀로 정리해 줘.', icon:Table2},
  {title:'문서에서 항목 뽑기', text:'영수증 PDF에서 날짜, 가맹점, 결제 금액을 뽑아 표로 만들어 줘.', icon:FileText},
];
const timestamp = new Intl.DateTimeFormat('ko-KR', {dateStyle:'medium', timeStyle:'short', timeZone:'Asia/Seoul'});

function timeLabel(value:string) {
  const time = Date.parse(value);
  return Number.isFinite(time) ? timestamp.format(time) : '';
}

export function WorkspaceOverview({memberName,workflows,loading,error,onStart,onNavigate,onRetry}:{
  memberName?:string;
  workflows:RecentWorkflow[];
  loading:boolean;
  error:string;
  onStart:(prompt:string,file?:File)=>void;
  onNavigate:(page:'workflows'|'workflow'|'nodes', service?:string)=>void;
  onRetry:()=>void;
}) {
  const [prompt,setPrompt]=useState('');
  const [file,setFile]=useState<File|null>(null);
  const [fileError,setFileError]=useState('');
  const fileInput=useRef<HTMLInputElement>(null);
  const canStart=!!prompt.trim()||!!file;

  return <div className="workspace-overview">
    <section className="overview-welcome" aria-labelledby="overview-title">
      {memberName && <p className="overview-greeting">{memberName}님, 안녕하세요</p>}
      <h1 id="overview-title">어떤 업무를 자동화할까요?</h1>
      <form className="overview-composer" data-tour="home-create" onSubmit={event=>{
        event.preventDefault();
        if(canStart)onStart(prompt.trim(),file??undefined);
      }}>
        <textarea aria-label="자동화할 업무" placeholder="예: 매출표에서 지점별 합계를 구해 엑셀로 정리해 줘." value={prompt}
          onChange={event=>setPrompt(event.target.value)}
          onKeyDown={event=>{
            if(event.key==='Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode!==229){
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          rows={2} maxLength={4000}/>
        {file && <div className="overview-attachment"><span>{file.name}</span><button type="button" aria-label="첨부 자료 제거" onClick={()=>setFile(null)}><X size={14}/></button></div>}
        {fileError && <p role="alert">{fileError}</p>}
        <input ref={fileInput} type="file" hidden accept=".csv,.tsv,.xlsx,.xls,.json,.txt,.md,.pdf,.doc,.docx,.ppt,.pptx,.hwp,.hwpx,.jpg,.jpeg,.png,.bmp,.webp,.tif,.tiff,.heic" onChange={event=>{
          const selected=event.currentTarget.files?.[0];event.currentTarget.value='';
          if(!selected)return;
          if(selected.size>8*1024*1024){setFileError('자료는 8MB 이하로 연결하세요.');return;}
          setFile(selected);setFileError('');
        }}/>
        <div className="overview-composer-actions">
          <button type="button" className="as-text-button" onClick={()=>fileInput.current?.click()}><Paperclip size={16}/>자료 추가</button>
          <small>Enter 전송 · Shift+Enter 줄바꿈</small>
          <button type="submit" className="as-button as-primary overview-submit" aria-label="업무 입력 확인" disabled={!canStart}>
            <ArrowUp size={17} aria-hidden="true"/>
          </button>
        </div>
      </form>
      <div className="overview-examples" aria-label="업무 예시">
        {examples.map(({title,text,icon:Icon})=><button type="button" key={title} onClick={()=>setPrompt(text)}>
          <Icon size={15} aria-hidden="true"/>{title}
        </button>)}
      </div>
    </section>
    <section className="overview-section" aria-labelledby="home-workflows">
      <div className="overview-section-heading">
        <h2 id="home-workflows">최근 워크플로우</h2>
        {workflows.length>0 && <button type="button" className="as-text-button" onClick={()=>onNavigate('workflows')}>전체 보기 <ArrowRight size={14} aria-hidden="true"/></button>}
      </div>
      {error ? <div className="as-error" role="alert">
        <span>최근 워크플로우를 불러오지 못했습니다.</span><button type="button" onClick={onRetry}>다시 시도</button>
      </div> : loading ? <p className="overview-empty" role="status">최근 작업을 불러오고 있습니다.</p> :
      workflows.length > 0 ? <ul>{workflows.map(workflow=><li key={workflow.id}>
        <button type="button" className="overview-row" onClick={()=>onNavigate('workflow',workflow.id)}>
          <span className="overview-icon"><Workflow size={17} aria-hidden="true"/></span>
          <span className="overview-item"><strong>{workflow.config.name}</strong><small>{timeLabel(workflow.updated_at)}</small></span>
          <ArrowRight size={15} aria-hidden="true"/>
        </button>
      </li>)}</ul> : <p className="overview-empty">저장된 워크플로우 없음</p>}
    </section>
    <section className="overview-section" aria-labelledby="home-library">
      <div className="overview-section-heading"><h2 id="home-library">작업 라이브러리</h2></div>
      <ul>
        <li><button type="button" className="overview-row" onClick={()=>onNavigate('workflows')}>
          <span className="overview-icon"><Workflow size={17} aria-hidden="true"/></span>
          <span className="overview-item"><strong>저장한 워크플로우</strong></span>
          <ArrowRight size={15} aria-hidden="true"/>
        </button></li>
        <li><button type="button" className="overview-row" onClick={()=>onNavigate('nodes')}>
          <span className="overview-icon"><Layers size={17} aria-hidden="true"/></span>
          <span className="overview-item"><strong>작업 템플릿</strong></span>
          <ArrowRight size={15} aria-hidden="true"/>
        </button></li>
      </ul>
    </section>
  </div>;
}

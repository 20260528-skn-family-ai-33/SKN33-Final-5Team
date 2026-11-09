import {GmailConnection} from './gmail-connection';
import './service-connections.css';

export function ServiceConnections() {
  return <section className="service-connections" aria-labelledby="service-connections-heading">
    <div className="entry-page-heading"><div>
      <h1 id="service-connections-heading">서비스 연결</h1>
      <p>워크플로우에서 사용할 외부 계정을 연결하고 관리하세요.</p>
    </div></div>
    <div className="service-connection-category"><h2>메일</h2><span>1개 서비스</span></div>
    <div className="service-connection-grid"><GmailConnection disabled={false} variant="card"/></div>
    <p className="service-connections-note">연결한 계정은 현재 작업 공간에서 사용합니다. 각 서비스에서 허용한 권한은 연결 관리에서 확인할 수 있어요.</p>
  </section>;
}

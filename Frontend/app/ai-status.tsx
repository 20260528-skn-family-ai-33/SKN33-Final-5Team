import {useEffect, useRef, useState} from 'react';
import {RefreshCw} from 'lucide-react';
import {Popover, PopoverContent, PopoverTrigger} from '@/components/ui/popover';
import './ai-status.css';

type Status = 'checking' | 'ready' | 'unavailable';
type Connection = {health?: {status?: string}};
const labels: Record<Status, string> = {
  checking: 'AI 연결 확인 중',
  ready: 'AI 사용 가능',
  unavailable: 'AI 일시 중단',
};

/** Check model availability separately from loading the workspace itself. */
export function AiStatus() {
  const [status, setStatus] = useState<Status>('checking');
  const [lastChecked, setLastChecked] = useState<Date>();
  const check = useRef<() => void>(() => {});

  useEffect(() => {
    let active = true;
    let request: AbortController | undefined;
    let timeout: number | undefined;
    let nextCheck: number | undefined;

    async function refresh() {
      if (!active || request) return;
      window.clearTimeout(nextCheck);
      const controller = new AbortController();
      request = controller;
      setStatus('checking');
      timeout = window.setTimeout(() => controller.abort(), 6500);
      try {
        const response = await fetch('/api/agent-builder/connection', {
          credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        });
        if (!response.ok) throw new Error('Connection unavailable');
        const value = await response.json() as Connection;
        if (active && !controller.signal.aborted) setStatus(value.health?.status === 'ready' ? 'ready' : 'unavailable');
      } catch {
        if (active) setStatus('unavailable');
      } finally {
        window.clearTimeout(timeout);
        request = undefined;
        if (active) {
          // An abort may happen while reading a response body as well as during fetch.
          if (controller.signal.aborted) setStatus('unavailable');
          setLastChecked(new Date());
          nextCheck = window.setTimeout(() => void refresh(), 60000);
        }
      }
    }

    check.current = () => void refresh();
    void refresh();
    return () => {
      active = false;
      check.current = () => {};
      window.clearTimeout(timeout);
      window.clearTimeout(nextCheck);
      request?.abort();
    };
  }, []);

  return <Popover>
    <PopoverTrigger asChild>
      <button type="button" className="ai-status-badge" data-status={status} aria-label={'AI 상태: ' + labels[status]}>
        <span className="ai-status-dot" aria-hidden="true"/>
        <span role="status" aria-live="polite">{labels[status]}</span>
      </button>
    </PopoverTrigger>
    <PopoverContent className="ai-status-details" align="end" sideOffset={10} aria-label="AI 연결 상태">
      <h2>AI 연결 상태</h2>
      <p>{status === 'ready' ? '워크플로우를 만들고 AI 작업을 실행할 수 있어요.'
        : status === 'checking' ? 'AI가 응답하는지 확인하고 있어요.'
        : 'AI 연결을 확인하지 못했어요. 잠시 후 다시 확인해주세요.'}</p>
      {lastChecked && <small>마지막 확인 {lastChecked.toLocaleTimeString('ko-KR', {hour: '2-digit', minute: '2-digit'})}</small>}
      <button type="button" className="as-button" disabled={status === 'checking'} onClick={() => check.current()}>
        <RefreshCw size={14} aria-hidden="true"/>다시 확인
      </button>
    </PopoverContent>
  </Popover>;
}

import {useEffect, useRef, useState} from 'react';
import {RefreshCw} from 'lucide-react';
import {Popover, PopoverContent, PopoverTrigger} from '@/components/ui/popover';
import './ai-status.css';

type Status = 'checking' | 'ready' | 'unavailable';
type Connection = {health?: {status?: string; target?: string; message?: string}};
const labels: Record<Status, string> = {
  checking: 'AI 연결 확인 중',
  ready: 'AI 사용 가능',
  unavailable: 'AI 일시 중단',
};

/** Show the configured shared server independently of a member's local model setup. */
export function AiStatus() {
  const [status, setStatus] = useState<Status>('checking');
  const [healthMessage, setHealthMessage] = useState('');
  const [server, setServer] = useState('RunPod');
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
      setHealthMessage('');
      timeout = window.setTimeout(() => controller.abort(), 6500);
      try {
        const response = await fetch('/api/agent-builder/connection', {
          credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        });
        if (!response.ok) throw new Error('Connection unavailable');
        const value = await response.json() as Connection;
        if (active && !controller.signal.aborted) {
          const target = value.health?.target;
          setServer(target === 'ec2' ? 'EC2' : 'RunPod');
          setStatus(value.health?.status === 'ready' && (target === 'runpod' || target === 'ec2') ? 'ready' : 'unavailable');
          setHealthMessage(typeof value.health?.message === 'string' ? value.health.message : '');
        }
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

    const refreshWhenVisible = () => {if (document.visibilityState === 'visible') void refresh();};
    check.current = () => void refresh();
    window.addEventListener('focus', refreshWhenVisible);
    document.addEventListener('visibilitychange', refreshWhenVisible);
    void refresh();
    return () => {
      active = false;
      check.current = () => {};
      window.clearTimeout(timeout);
      window.clearTimeout(nextCheck);
      window.removeEventListener('focus', refreshWhenVisible);
      document.removeEventListener('visibilitychange', refreshWhenVisible);
      request?.abort();
    };
  }, []);

  return <Popover>
    <PopoverTrigger asChild>
      <button type="button" className="ai-status-badge" data-status={status} aria-label={'공용 '+server+' AI 서버 상태: ' + labels[status]}>
        <span className="ai-status-dot" aria-hidden="true"/>
        <span role="status" aria-live="polite">{labels[status]}</span>
      </button>
    </PopoverTrigger>
    <PopoverContent className="ai-status-details" align="end" sideOffset={10} aria-label={'공용 '+server+' AI 서버 상태'}>
      <h2>공용 {server} AI 서버 상태</h2>
      <p>{status === 'ready' ? '공용 '+server+' AI 서버에 연결되어 AI 작업을 실행할 수 있어요.'
        : status === 'checking' ? '공용 '+server+' AI 서버가 응답하는지 확인하고 있어요.'
        : healthMessage || '공용 '+server+' AI 서버에 연결하지 못했어요. 잠시 후 다시 확인해주세요.'}</p>
      {lastChecked && <small>마지막 확인 {lastChecked.toLocaleTimeString('ko-KR', {hour: '2-digit', minute: '2-digit'})}</small>}
      <button type="button" className="as-button" disabled={status === 'checking'} onClick={() => check.current()}>
        <RefreshCw size={14} aria-hidden="true"/>다시 확인
      </button>
    </PopoverContent>
  </Popover>;
}

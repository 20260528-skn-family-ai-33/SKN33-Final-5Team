import type { FlowNode } from '../lib/workflow';

type Local = { year: number; month: number; day: number; hour: number; minute: number; second: number };
type Rule = Record<string, unknown>;
type Cron = { minutes: number[]; hours: number[]; days: number[]; months: number[]; weekdays: number[]; anyDay: boolean; anyWeekday: boolean };
const DAY = 86_400_000;
const formatters = new Map<string, Intl.DateTimeFormat>();
const monthNames = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
const weekdayNames = ['SUN','MON','TUE','WED','THU','FRI','SAT'];

function formatter(timezone: string): Intl.DateTimeFormat {
  if (typeof timezone !== 'string' || !timezone.trim()) throw new Error('예약 시간대를 선택해주세요.');
  let result = formatters.get(timezone);
  if (!result) {
    try {
      result = new Intl.DateTimeFormat('en-GB', { timeZone: timezone, calendar: 'gregory', numberingSystem: 'latn', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
      result.format(0);
    } catch { throw new Error('지원하지 않는 예약 시간대예요.'); }
    formatters.set(timezone, result);
  }
  return result;
}

function localAt(instant: number, timezone: string): Local {
  const result: Record<string, number> = {};
  for (const part of formatter(timezone).formatToParts(instant)) if (part.type !== 'literal') result[part.type] = Number(part.value);
  return result as Local;
}

function dateStamp(date: Pick<Local, 'year'|'month'|'day'>): number { return Date.UTC(date.year, date.month - 1, date.day); }
function dateAt(stamp: number): Pick<Local, 'year'|'month'|'day'> {
  const date = new Date(stamp);
  return { year: date.getUTCFullYear(), month: date.getUTCMonth() + 1, day: date.getUTCDate() };
}
function weekday(date: Pick<Local, 'year'|'month'|'day'>): number { return new Date(dateStamp(date)).getUTCDay(); }
function modulo(value: number, divisor: number): number { return ((value % divisor) + divisor) % divisor; }

// Resolve a wall-clock time through the offsets on both sides of a DST change.
// Nonexistent local times are skipped; repeated local times run once, at the first occurrence.
function wallTime(date: Pick<Local, 'year'|'month'|'day'>, hour: number, minute: number, timezone: string): number | undefined {
  const wall = dateStamp(date) + hour * 3_600_000 + minute * 60_000;
  const offsets = new Set<number>();
  for (const delta of [-36, -12, 0, 12, 36]) {
    const instant = wall + delta * 3_600_000;
    const parts = localAt(instant, timezone);
    offsets.add(dateStamp(parts) + parts.hour * 3_600_000 + parts.minute * 60_000 + parts.second * 1_000 - instant);
  }
  const candidates: number[] = [];
  for (const offset of offsets) {
    const instant = wall - offset;
    const actual = localAt(instant, timezone);
    if (actual.year === date.year && actual.month === date.month && actual.day === date.day && actual.hour === hour && actual.minute === minute && actual.second === 0) candidates.push(instant);
  }
  return candidates.length ? Math.min(...candidates) : undefined;
}

function integer(value: unknown, fallback: number, minimum: number, maximum: number, label: string): number {
  const number = value === undefined ? fallback : typeof value === 'number' ? value : typeof value === 'string' && /^\d+$/.test(value) ? Number(value) : NaN;
  if (!Number.isInteger(number) || number < minimum || number > maximum) throw new Error(`${label}은 ${minimum}~${maximum} 사이의 정수로 입력해주세요.`);
  return number;
}

function rules(node: FlowNode): Rule[] {
  if (node.type !== 'n8n-nodes-base.scheduleTrigger' && node.type !== 'easyflow.scheduleTrigger') throw new Error('시간 예약 시작 단계를 선택해주세요.');
  const intervals = node.parameters?.rule?.interval;
  if (!Array.isArray(intervals) || !intervals.length || intervals.length > 20 || intervals.some(rule => !rule || typeof rule !== 'object' || Array.isArray(rule))) throw new Error('예약 규칙을 1~20개 설정해주세요.');
  return intervals;
}

function cronExpression(rule: Rule, node: FlowNode): unknown { return rule.customExpression ?? rule.expression ?? node.parameters.customExpression; }
function cronField(text: string, min: number, max: number, names: string[] = [], namesStart = 0, sunday = false): number[] {
  const values = new Set<number>();
  const parse = (token: string): number => {
    const named = names.indexOf(token.toUpperCase());
    const number = named >= 0 ? named + namesStart : /^\d+$/.test(token) ? Number(token) : NaN;
    if (!Number.isInteger(number) || number < min || number > max) throw new Error('Cron 값의 범위가 잘못됐어요.');
    return number;
  };
  for (const segment of text.split(',')) {
    const pieces = segment.split('/');
    if (pieces.length > 2 || !pieces[0]) throw new Error('Cron 형식을 확인해주세요.');
    const step = pieces.length === 2 ? integer(pieces[1], 1, 1, max - min + 1, 'Cron 간격') : 1;
    let start: number; let end: number;
    if (pieces[0] === '*') { start = min; end = max; }
    else {
      const range = pieces[0].split('-');
      if (range.length > 2 || range.some(token => !token)) throw new Error('Cron 범위 형식을 확인해주세요.');
      start = parse(range[0]); end = range.length === 2 ? parse(range[1]) : pieces.length === 2 ? max : start;
      if (start > end) throw new Error('Cron 범위의 시작은 끝보다 작거나 같아야 해요.');
    }
    for (let value = start; value <= end; value += step) values.add(sunday && value === 7 ? 0 : value);
  }
  if (!values.size) throw new Error('Cron 값이 비어 있어요.');
  return [...values].sort((a,b) => a - b);
}

function parseCron(expression: unknown): Cron {
  if (typeof expression !== 'string') throw new Error('Cron 예약식을 입력해주세요.');
  const fields = expression.trim().split(/\s+/);
  if (fields.length !== 5) throw new Error('Cron은 분·시·일·월·요일의 5개 항목으로 입력해주세요.');
  const cron: Cron = { minutes: cronField(fields[0], 0, 59), hours: cronField(fields[1], 0, 23), days: cronField(fields[2], 1, 31), months: cronField(fields[3], 1, 12, monthNames, 1), weekdays: cronField(fields[4], 0, 7, weekdayNames, 0, true), anyDay: fields[2].startsWith('*'), anyWeekday: fields[4].startsWith('*') };
  // Check the complete Gregorian leap-year cycle without evaluating 400 years of minutes.
  let possible = false;
  for (let year = 2000; year < 2400 && !possible; year++) for (const month of cron.months) {
    const end = new Date(Date.UTC(year, month, 0)).getUTCDate();
    for (let day = 1; day <= end; day++) if (cronDateMatches(cron, {year, month, day})) { possible = true; break; }
  }
  if (!possible) throw new Error('실제로 존재하는 날짜에 실행되는 Cron 예약식을 입력해주세요.');
  return cron;
}

function cronDateMatches(cron: Cron, date: Pick<Local, 'year'|'month'|'day'>): boolean {
  if (!cron.months.includes(date.month)) return false;
  const day = cron.days.includes(date.day); const dow = cron.weekdays.includes(weekday(date));
  return cron.anyDay || cron.anyWeekday ? day && dow : day || dow;
}

function selectedWeekdays(rule: Rule): number[] {
  const days = rule.triggerAtDay === undefined ? [0] : rule.triggerAtDay;
  if (!Array.isArray(days) || !days.length || days.length > 7) throw new Error('실행할 요일을 하나 이상 선택해주세요.');
  return [...new Set(days.map(value => integer(value, 0, 0, 6, '요일')))];
}

function validateRule(rule: Rule, node: FlowNode): void {
  const field = rule.field ?? 'days';
  switch (field) {
    case 'seconds': integer(rule.secondsInterval, 30, 1, 59, '초 간격'); break;
    case 'minutes': integer(rule.minutesInterval, 5, 1, 59, '분 간격'); break;
    case 'hours': integer(rule.hoursInterval, 1, 1, 24, '시간 간격'); if (rule.triggerAtMinute !== undefined) integer(rule.triggerAtMinute, 0, 0, 59, '분'); break;
    case 'days': integer(rule.daysInterval, 1, 1, 366, '날짜 간격'); break;
    case 'weeks': integer(rule.weeksInterval, 1, 1, 104, '주 간격'); selectedWeekdays(rule); break;
    case 'months': integer(rule.monthsInterval, 1, 1, 120, '월 간격'); integer(rule.triggerAtDayOfMonth, 1, 1, 31, '날짜'); break;
    case 'cronExpression': parseCron(cronExpression(rule, node)); break;
    default: throw new Error('지원하지 않는 예약 간격이에요.');
  }
  if (['days','weeks','months'].includes(String(field))) {
    integer(rule.triggerAtHour, 0, 0, 23, '시'); integer(rule.triggerAtMinute, 0, 0, 59, '분');
  }
}

/** Validates the persisted trigger configuration before a workflow is activated. */
export function validateSchedule(node: FlowNode, timezone: string): void {
  formatter(timezone);
  for (const rule of rules(node)) validateRule(rule, node);
  if (node.scheduleAnchorAt !== undefined && (typeof node.scheduleAnchorAt !== 'number' || !Number.isFinite(node.scheduleAnchorAt) || Number.isNaN(new Date(node.scheduleAnchorAt).getTime()))) throw new Error('예약 기준 시각이 잘못됐어요.');
}

function nextRule(rule: Rule, node: FlowNode, after: number, timezone: string): number {
  const field = rule.field ?? 'days';
  const anchor = node.scheduleAnchorAt ?? after;
  if (field === 'seconds' || field === 'minutes' || field === 'hours' && rule.triggerAtMinute === undefined) {
    const interval = field === 'seconds' ? integer(rule.secondsInterval, 30, 1, 59, '초 간격') * 1000 : field === 'minutes' ? integer(rule.minutesInterval, 5, 1, 59, '분 간격') * 60_000 : integer(rule.hoursInterval, 1, 1, 24, '시간 간격') * 3_600_000;
    return anchor + Math.max(1, Math.floor((after - anchor) / interval) + 1) * interval;
  }
  if (field === 'hours') {
    const startParts = localAt(anchor, timezone);
    const targetMinute = integer(rule.triggerAtMinute, 0, 0, 59, '분');
    const start = Math.floor(anchor / 60_000) * 60_000 + (targetMinute - startParts.minute) * 60_000;
    const interval = integer(rule.hoursInterval, 1, 1, 24, '시간 간격') * 3_600_000;
    const first = start > anchor ? start : start + interval;
    return first + Math.max(0, Math.floor((after - first) / interval) + 1) * interval;
  }
  const startDate = localAt(after, timezone);
  const anchorDate = localAt(anchor, timezone);
  const start = dateStamp(startDate); const anchorDay = dateStamp(anchorDate);
  const cron = field === 'cronExpression' ? parseCron(cronExpression(rule, node)) : undefined;
  const daysInterval = integer(rule.daysInterval, 1, 1, 366, '날짜 간격');
  const weeksInterval = integer(rule.weeksInterval, 1, 1, 104, '주 간격');
  const monthsInterval = integer(rule.monthsInterval, 1, 1, 120, '월 간격');
  const weekdays = field === 'weeks' ? selectedWeekdays(rule) : [];
  const mondayAnchor = anchorDay - modulo(weekday(anchorDate) - 1, 7) * DAY;
  const anchorMonth = anchorDate.year * 12 + anchorDate.month - 1;
  const monthDay = integer(rule.triggerAtDayOfMonth, 1, 1, 31, '날짜');
  const hours = cron?.hours ?? [integer(rule.triggerAtHour, 0, 0, 23, '시')];
  const minutes = cron?.minutes ?? [integer(rule.triggerAtMinute, 0, 0, 59, '분')];
  const horizon = field === 'months' ? Math.max(monthsInterval * 31 * 9,366 * 9) : 366 * 9;
  for (let dayOffset = 0; dayOffset <= horizon; dayOffset++) {
    const stamp = start + dayOffset * DAY; const date = dateAt(stamp);
    let matches: boolean;
    if (cron) matches = cronDateMatches(cron, date);
    else if (field === 'days') matches = modulo(Math.round((stamp - anchorDay) / DAY), daysInterval) === 0;
    else if (field === 'weeks') matches = modulo(Math.floor((stamp - mondayAnchor) / (7 * DAY)), weeksInterval) === 0 && weekdays.includes(weekday(date));
    else matches = modulo(date.year * 12 + date.month - 1 - anchorMonth, monthsInterval) === 0 && date.day === monthDay;
    if (!matches) continue;
    for (const hour of hours) for (const minute of minutes) {
      if (dayOffset === 0 && (hour < startDate.hour || hour === startDate.hour && minute < startDate.minute)) continue;
      const instant = wallTime(date, hour, minute, timezone);
      if (instant !== undefined && instant > after && instant >= anchor) return instant;
    }
  }
  throw new Error('다음 예약 시각을 찾을 수 없어요. 날짜와 예약식을 확인해주세요.');
}

/** Earliest UTC epoch millisecond strictly after `after`, across all trigger rules.
 * Persist scheduleAnchorAt (activation time) for stable multi-day/week/month intervals.
 */
export function nextRunAt(node: FlowNode, after: number, timezone = 'Asia/Seoul'): number {
  if (!Number.isFinite(after) || Number.isNaN(new Date(after).getTime()) || new Date(after).getUTCFullYear() < 100 || new Date(after).getUTCFullYear() > 9980) throw new Error('예약 계산 기준 시각이 잘못됐어요.');
  validateSchedule(node, timezone);
  return Math.min(...rules(node).map(rule => nextRule(rule, node, after, timezone)));
}

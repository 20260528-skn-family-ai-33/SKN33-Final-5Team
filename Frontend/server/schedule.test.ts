import assert from 'node:assert/strict';
import test from 'node:test';
import type { FlowNode } from '../lib/workflow';
import { nextRunAt, validateSchedule } from './schedule';

function node(interval: Record<string, unknown> | Record<string, unknown>[], anchor?: string): FlowNode {
  return { id: 'schedule', name: '시간 예약', type: 'n8n-nodes-base.scheduleTrigger', typeVersion: 1.2, position: [0,0], parameters: { rule: { interval: Array.isArray(interval) ? interval : [interval] } }, ...(anchor ? {scheduleAnchorAt: Date.parse(anchor)} : {}) };
}
function next(trigger: FlowNode, after: string, timezone?: string): string { return new Date(nextRunAt(trigger, Date.parse(after), timezone)).toISOString(); }

test('daily 9 AM Seoul is midnight UTC and the boundary is exclusive', () => {
  const trigger = node({field:'days', daysInterval:1, triggerAtHour:9, triggerAtMinute:0});
  assert.equal(next(trigger, '2026-10-06T23:59:59.999Z'), '2026-10-07T00:00:00.000Z');
  assert.equal(next(trigger, '2026-10-07T00:00:00.000Z'), '2026-10-08T00:00:00.000Z');
  assert.equal(next(trigger, '2026-10-07T14:59:59.000Z'), '2026-10-08T00:00:00.000Z');
});

test('30-second and 5-minute intervals retain their activation phase after late ticks', () => {
  const anchor = '2026-10-07T00:00:05.000Z';
  assert.equal(next(node({field:'seconds',secondsInterval:30}), anchor), '2026-10-07T00:00:35.000Z');
  assert.equal(next(node({field:'seconds',secondsInterval:30}, anchor), '2026-10-07T00:01:09.000Z'), '2026-10-07T00:01:35.000Z');
  assert.equal(next(node({field:'minutes',minutesInterval:5}, anchor), '2026-10-07T00:00:06.000Z'), '2026-10-07T00:05:05.000Z');
});

test('hour intervals support elapsed hours and an explicit minute', () => {
  assert.equal(next(node({field:'hours',hoursInterval:2}), '2026-10-07T00:07:12.000Z'), '2026-10-07T02:07:12.000Z');
  const trigger = node({field:'hours',hoursInterval:2,triggerAtMinute:30}, '2026-10-07T00:07:12.000Z');
  assert.equal(next(trigger, '2026-10-07T00:07:12.000Z'), '2026-10-07T00:30:00.000Z');
  assert.equal(next(trigger, '2026-10-07T00:30:00.000Z'), '2026-10-07T02:30:00.000Z');
});

test('multi-day schedules preserve their activation date', () => {
  const trigger = node({field:'days',daysInterval:2,triggerAtHour:9}, '2026-10-07T01:00:00.000Z');
  assert.equal(next(trigger, '2026-10-07T01:00:00.000Z'), '2026-10-09T00:00:00.000Z');
  assert.equal(next(trigger, '2026-10-09T00:00:00.000Z'), '2026-10-11T00:00:00.000Z');
});

test('Monday 9 AM and selected weekdays with two-week intervals', () => {
  assert.equal(next(node({field:'weeks',triggerAtDay:[1],triggerAtHour:9}), '2026-10-07T02:00:00.000Z'), '2026-10-12T00:00:00.000Z');
  const trigger = node({field:'weeks',weeksInterval:2,triggerAtDay:[1,4],triggerAtHour:9}, '2026-10-05T00:00:00.000Z');
  assert.equal(next(trigger, '2026-10-05T00:00:00.000Z'), '2026-10-08T00:00:00.000Z');
  assert.equal(next(trigger, '2026-10-08T00:00:00.000Z'), '2026-10-19T00:00:00.000Z');
});

test('monthly schedules skip missing dates and preserve month intervals', () => {
  assert.equal(next(node({field:'months',triggerAtDayOfMonth:31,triggerAtHour:9}), '2026-01-31T00:00:00.000Z'), '2026-03-31T00:00:00.000Z');
  const trigger = node({field:'months',monthsInterval:2,triggerAtDayOfMonth:15,triggerAtHour:9}, '2026-01-10T00:00:00.000Z');
  assert.equal(next(trigger, '2026-01-15T00:00:00.000Z'), '2026-03-15T00:00:00.000Z');
});

test('five-field cron supports weekdays, names, lists, ranges and steps', () => {
  assert.equal(next(node({field:'cronExpression',expression:'0 9 * * 1-5'}), '2026-10-09T00:00:00.000Z'), '2026-10-12T00:00:00.000Z');
  assert.equal(next(node({field:'cronExpression',customExpression:'*/15 9,10 * OCT MON-FRI'}), '2026-10-07T00:16:00.000Z'), '2026-10-07T00:30:00.000Z');
  assert.equal(next(node({field:'cronExpression',expression:'0 9 * * 7'}), '2026-10-07T00:00:00.000Z'), '2026-10-11T00:00:00.000Z');
});

test('cron follows standard day-of-month OR weekday semantics when both are constrained', () => {
  assert.equal(next(node({field:'cronExpression',expression:'0 9 1 * MON'}), '2026-10-07T00:00:00.000Z'), '2026-10-12T00:00:00.000Z');
  assert.equal(next(node({field:'cronExpression',expression:'0 9 */2 * MON'}), '2026-10-07T00:00:00.000Z'), '2026-10-19T00:00:00.000Z');
});

test('multiple rules pick the earliest next run', () => {
  const trigger = node([{field:'days',triggerAtHour:10},{field:'days',triggerAtHour:9}]);
  assert.equal(next(trigger, '2026-10-06T23:00:00.000Z'), '2026-10-07T00:00:00.000Z');
});

test('Intl timezones handle DST gaps and run a repeated local time once', () => {
  const gap = node({field:'days',triggerAtHour:2,triggerAtMinute:30});
  assert.equal(next(gap, '2026-03-08T05:00:00.000Z', 'America/New_York'), '2026-03-09T06:30:00.000Z');
  const overlap = node({field:'days',triggerAtHour:1,triggerAtMinute:30});
  assert.equal(next(overlap, '2026-11-01T04:00:00.000Z', 'America/New_York'), '2026-11-01T05:30:00.000Z');
  assert.equal(next(overlap, '2026-11-01T05:30:00.000Z', 'America/New_York'), '2026-11-02T06:30:00.000Z');
});

test('invalid settings, unsupported cron syntax and impossible dates are rejected', () => {
  const invalid: Record<string,unknown>[] = [
    {field:'seconds',secondsInterval:0}, {field:'minutes',minutesInterval:1.5}, {field:'hours',hoursInterval:-1},
    {field:'days',triggerAtHour:24}, {field:'days',triggerAtMinute:60}, {field:'weeks',triggerAtDay:[]},
    {field:'weeks',triggerAtDay:[7]}, {field:'months',triggerAtDayOfMonth:32}, {field:'unknown'},
    {field:'cronExpression',expression:'0 0 9 * * *'}, {field:'cronExpression',expression:'60 9 * * *'},
    {field:'cronExpression',expression:'0 9 30 FEB *'}, {field:'cronExpression',expression:'*/0 * * * *'},
    {field:'cronExpression',expression:'0 9 * * FRI-MON'}, {field:'cronExpression',expression:'0 9 ? * *'},
  ];
  for (const rule of invalid) assert.throws(() => validateSchedule(node(rule), 'Asia/Seoul'), JSON.stringify(rule));
  assert.throws(() => validateSchedule(node({field:'days'}), 'Not/A_Timezone'));
  assert.throws(() => validateSchedule(node([]), 'Asia/Seoul'));
  assert.throws(() => nextRunAt(node({field:'days'}), NaN));
  const invalidAnchor = node({field:'days'}); invalidAnchor.scheduleAnchorAt = 1e30;
  assert.throws(() => validateSchedule(invalidAnchor, 'Asia/Seoul'));
});

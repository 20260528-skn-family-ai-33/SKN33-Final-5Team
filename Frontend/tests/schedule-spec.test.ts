import assert from 'node:assert/strict';
import test from 'node:test';
import {scheduleDescription,type ScheduleSpec} from '../lib/schedule-spec';

const base:ScheduleSpec={frequency:'daily',timezone:'Asia/Seoul',hour:9,minute:0,weekday:null};
for(const [change,expected] of [
 [{frequency:'hourly',hour:null},'매시간 정각'],
 [{frequency:'hourly',hour:null,minute:15},'매시간 15분'],
 [{frequency:'monthly',monthday:31},'매월 31일 09:00'],
 [{frequency:'monthly',monthday:null,month_end:true},'매월 말일 09:00'],
 [{frequency:'weekly',weekday:4},'매주 금요일 09:00'],
 [{},'매일 09:00'],
] as const)test('schedule display: '+expected,()=>{
 assert.equal(scheduleDescription({...base,...change}),expected);
});
test('incomplete monthly and hourly times are visibly unresolved',()=>{
 assert.equal(scheduleDescription({...base,frequency:'monthly'}),'매월 실행일 확인 필요');
 assert.equal(scheduleDescription({...base,frequency:'hourly',hour:null,minute:null}),'실행 시간 확인 필요');
});

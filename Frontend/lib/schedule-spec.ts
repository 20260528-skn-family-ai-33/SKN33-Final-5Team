export type ScheduleSpec={
 frequency:'hourly'|'daily'|'weekly'|'monthly';
 timezone:string|null;
 hour:number|null;
 minute:number|null;
 weekday:number|null;
 monthday?:number|null;
 month_end?:boolean;
};

export function scheduleDescription(spec:ScheduleSpec):string {
 const {hour,minute}=spec;
 if(typeof minute!=='number'||!Number.isInteger(minute)||minute<0||minute>59)return '실행 시간 확인 필요';
 if(spec.frequency==='hourly')return minute===0?'매시간 정각':'매시간 '+minute+'분';
 if(typeof hour!=='number'||!Number.isInteger(hour)||hour<0||hour>23)return '실행 시간 확인 필요';
 const time=String(hour).padStart(2,'0')+':'+String(minute).padStart(2,'0');
 if(spec.frequency==='daily')return '매일 '+time;
 if(spec.frequency==='monthly'){
  if(spec.month_end)return '매월 말일 '+time;
  if(typeof spec.monthday==='number'&&Number.isInteger(spec.monthday)&&spec.monthday>=1&&spec.monthday<=31)return '매월 '+spec.monthday+'일 '+time;
  return '매월 실행일 확인 필요';
 }
 if(spec.frequency==='weekly'){
  const weekday=spec.weekday===null?undefined:['월','화','수','목','금','토','일'][spec.weekday];
  return weekday?'매주 '+weekday+'요일 '+time:'반복 주기 확인 필요';
 }
 return '반복 주기 확인 필요';
}

export function scheduleDateNote(spec:ScheduleSpec):string {
 return spec.frequency==='monthly'&&!spec.month_end&&(spec.monthday||0)>=29?'해당 날짜가 없는 달에는 실행하지 않습니다.':'';
}

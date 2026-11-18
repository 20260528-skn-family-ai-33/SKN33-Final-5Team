type Rule = Record<string, unknown>;

/** Imported workflows can store Cron at the node level or under either rule key. */
export function scheduleCronExpression(rule:Rule,parameters:Rule):unknown {
 return rule.customExpression ?? rule.expression ?? parameters.customExpression;
}

/** A frequency switch starts with fields that the selected frequency can use. */
export function scheduleRuleForField(field:string,previous:Rule={},parameters:Rule={}):Rule {
 const clock={triggerAtHour:previous.triggerAtHour??9,triggerAtMinute:previous.triggerAtMinute??0};
 switch(field){
  case 'seconds':return {field,secondsInterval:30};
  case 'minutes':return {field,minutesInterval:5};
  case 'hours':return {field,hoursInterval:1};
  case 'weeks':return {field,weeksInterval:1,triggerAtDay:[0],...clock};
  case 'months':return {field,monthsInterval:1,triggerAtDayOfMonth:1,...clock};
  case 'cronExpression':return {field,customExpression:scheduleCronExpression(previous,parameters)??'0 9 * * *'};
  default:return {field:'days',daysInterval:1,...clock};
 }
}

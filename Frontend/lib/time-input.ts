export function normalizeTimeInput(value:string):string|null {
 const match=/^([01]?\d|2[0-3]):([0-5]\d)$/.exec(value.trim());
 return match?match[1].padStart(2,'0')+':'+match[2]:null;
}

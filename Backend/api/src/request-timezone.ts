/** Optional browser IANA zone. Absence preserves legacy application defaults. */
export function parseRequestTimezone(value: unknown): string | undefined {
 if(value===undefined)return undefined;
 if(typeof value!=='string'||!value||value.length>100)throw new Error('요청 시간대를 확인하세요.');
 try {new Intl.DateTimeFormat('en',{timeZone:value});}
 catch {throw new Error('요청 시간대를 확인하세요.');}
 return value;
}

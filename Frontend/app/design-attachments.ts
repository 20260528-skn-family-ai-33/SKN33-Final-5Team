export const MAX_DESIGN_ATTACHMENTS=10;
export function designAttachmentError(files:readonly File[],existing=0):string {
 if(existing+files.length>MAX_DESIGN_ATTACHMENTS)return '자료는 최대 10개 연결할 수 있습니다.';
 if(files.some(file=>file.size>8*1024*1024))return '자료는 파일당 8MB 이하로 연결하세요.';
 return '';
}

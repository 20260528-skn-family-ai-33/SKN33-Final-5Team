import type {ClarificationQuestionData} from '../app/clarification-question';

export const folderWord = /폴더|디렉터리|\b(?:folder|directory)\b/i;
const otherSpace = /팀\s*(?:자료|폴더|디렉터리)|공유\s*드라이브|구글\s*드라이브|로컬|바탕\s*화면|(?:[a-z]:\\|\/(?:home|tmp)\/)/i;
const destination = /(?:저장|출력|내보낼|생성할|옮길|이동할)\s*(?:할\s*)?(?:위치|경로|폴더|디렉터리)|(?:폴더|디렉터리)(?:에|로)\s*(?:결과[를물]?\s*)?(?:저장|출력|내보내|옮|이동|생성)/;
const whole = /(?:전체|모든)\s*(?:파일|문서|자료|내용)|(?:파일|문서|자료)(?:들)?[을를]?\s*(?:전체|모두|전부|다\s)|(?:폴더|디렉터리)\s*(?:안|내|속)?(?:에서|에|의)?\s*(?:전체|모두|전부)|\b(?:all|every)\s+(?:the\s+)?(?:files?|documents?)\b/i;
const subset = /제외|빼고|말고|아니라|일부|하위\s*(?:폴더|디렉터리)|최신|최근|중에서|중\s|만(?:\s|읽|골라|뽑|처리|요약|추출|사용)|\bonly\b|\bexcept\b/i;

export function folderInputMode(text:string,answeringSource=false):'all'|'pick'|undefined {
  const marker = folderWord.exec(text);
  if (!marker || otherSpace.test(text)) return;
  const tail = text.slice(marker.index + marker[0].length);
  const sourceLocation = /^\s*(?:에서|(?:안|속)(?:에|의|에서)?|내(?:의|에서|\s|$)|에\s*(?:있는|담긴|저장된)|(?:의\s*)?(?:파일|문서|자료)(?:들)?(?:로|를|을|에서|만)|(?:을|를)?\s*(?:기반|바탕|참고|이용|활용))/.test(tail);
  const readAction = /읽|요약|분석|추출|검토|집계|비교|처리|취합|정리|검색|찾아|합[치쳐]|참조|참고|불러|가져|기반|기준|대상|사용|\b(?:read|summari[sz]e|analy[sz]e|extract|process|use|search)\b/i;
  if (!sourceLocation && destination.test(text)) return;
  if (/(?:읽지|사용하지)\s*(?:마|말|않)|(?:폴더|디렉터리)[를을]?\s*(?:만들|삭제|이동|생성|변경)/.test(text)) return;
  if (!sourceLocation && !readAction.test(text) && !answeringSource) return;
  const directRead = /^(?:[를을]?\s*읽|(?:\s*(?:안|내|속))?(?:에서|에|의)?\s*(?:있는\s*)?(?:파일|문서|자료)(?:들)?[을를]?\s*읽)/.test(tail);
  return !subset.test(text) && (whole.test(text) || directRead) ? 'all' : 'pick';
}

// New questions carry the input key. Older checkpoints can have model-written
// keys and natural-language questions instead.
export function libraryQuestion(question:ClarificationQuestionData):'files'|'folder'|undefined {
  const {key='',text} = question;
  if (/^(?:output|destination|export)[._]/i.test(key) || otherSpace.test(text) || destination.test(text)) return;
  if (question.response?.type==='number'||question.response?.type==='time') return;
  if (/(?:^|[._])(?:file|asset|document)_ids?$/i.test(key) ||
      /(?:파일|자료|문서)(?:들)?(?:의)?\s*(?:아이디|식별자|ID)|(?:file|asset|document)[_ ]ids?/i.test(text)) return 'files';
  if (/^(?:(?:input|source|library)[._])?(?:folder|directory)(?:[._](?:id|name|path))?$/i.test(key)) return 'folder';
  if (!folderWord.test(text) || /(?:파일|문서|자료|폴더)(?:의)?\s*(?:형식|확장자|개수|개씩|유형)|어떤\s*(?:형식|기준|시간|순서|내용)/.test(text)) return;
  if (/(?:어떤|어느|무슨)\s*(?:입력\s*|자료\s*)?(?:폴더|디렉터리)|(?:폴더|디렉터리)(?:의)?\s*(?:이름|경로|위치|ID|아이디|식별자)|(?:폴더|디렉터리)[은는이가]?\s*(?:어디|무엇|어느)|(?:폴더|디렉터리)[를을]?\s*(?:알려|지정|선택|골라)|(?:which|what)\s+(?:source\s+)?(?:folder|directory)/i.test(text)) return 'folder';
  if (/(?:어떤|어느|무슨)\s*(?:파일|문서|자료)|(?:파일|문서|자료)(?:들)?[을를]\s*(?:선택|지정|골라|알려)|which\s+files?/i.test(text)) return 'files';
}

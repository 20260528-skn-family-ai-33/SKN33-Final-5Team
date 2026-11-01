import ts from 'typescript';

export const CODE_CONTRACT='transform-function-v2';
export class SourceContractError extends Error{}

/** Format gate only. Parsing is not a security boundary; execution stays in QuickJS. */
export function executableSource(code:string):string{
 try{return checkedSource(code);}
 catch(error){
  if(error instanceof SourceContractError)throw error;
  throw new SourceContractError('코드 구조를 파싱하지 못했습니다. 지나치게 깊은 중첩을 줄이고 함수 규격을 확인하세요.');
 }
}

function checkedSource(code:string):string{
 if(code.length>20000)throw new SourceContractError('코드는 20,000자 이내여야 합니다.');
 const source=ts.createSourceFile('candidate.js',code,ts.ScriptTarget.ES2022,true,ts.ScriptKind.JS);
 const diagnostics=(source as ts.SourceFile&{parseDiagnostics:readonly ts.Diagnostic[]}).parseDiagnostics;
 if(diagnostics.length)throw new SourceContractError('JavaScript 문법 오류입니다. 실제 줄바꿈을 사용하고 유효한 함수 선언을 반환하세요.');
 if(source.statements.length!==1||!ts.isFunctionDeclaration(source.statements[0]))
  throw new SourceContractError('최상위에는 function transform(rows) 함수 선언 하나만 작성하세요. 호출·return은 실행기가 추가합니다.');
 const fn=source.statements[0];
 if(fn.name?.text!=='transform'||!fn.body||fn.asteriskToken||fn.modifiers?.length||fn.parameters.length!==1||
    !ts.isIdentifier(fn.parameters[0].name)||fn.parameters[0].initializer||fn.parameters[0].dotDotDotToken||fn.parameters[0].questionToken)
  throw new SourceContractError('동기식 function transform(rows) 함수가 필요합니다. 인자는 원본 행 객체 배열 하나입니다.');
 let invalid=false;
 const inspect=(node:ts.Node,depth=0)=>{
  if(depth>200)throw new SourceContractError('코드 중첩이 너무 깊습니다. 함수 구조를 단순화하세요.');
  const typed=node as ts.Node&{type?:ts.Node;typeArguments?:readonly ts.Node[]};
  if(ts.isIdentifier(node)&&['$input','$json','$items','$node'].includes(node.text)||typed.type||typed.typeArguments?.length||
     ts.isAsExpression(node)||ts.isTypeAssertionExpression(node))invalid=true;
  ts.forEachChild(node,child=>inspect(child,depth+1));
 };
 inspect(fn);
 if(invalid)throw new SourceContractError('TypeScript 문법과 $input 등 실행기 API를 사용하지 마세요. rows의 각 요소가 원본 행입니다.');
 return code+'\nreturn transform($input.all().map(item => item.json));';
}

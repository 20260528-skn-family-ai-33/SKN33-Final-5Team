export type QuestionResponse={type:'single_choice'|'multi_choice'|'number'|'text'|'time';minimum?:number;maximum?:number;step?:number;unit?:string};
export type ClarificationQuestionData={id?:string;key?:string;text:string;options?:string[];response?:QuestionResponse;group?:string|null;reason?:string|null;required?:boolean};

import {Braces,Calculator,CalendarDays,CheckCircle2,ClipboardCheck,Database,FileDown,FilePenLine,FileSpreadsheet,FileText,Filter,GitBranch,Globe,Layers,Mail,Merge,MessageSquare,ScanText,Search,ShieldCheck,Sparkles,Type,Workflow,Languages,Scissors,Link,Archive,Image,FileCheck} from 'lucide-react';
const icons:Record<string,typeof FileText>={
 read_document:FileText,read_table:FileSpreadsheet,search_document:Search,extract_information:ScanText,
 compose_answer:FilePenLine,agent_node:Sparkles,filter_sort:Filter,calculate_columns:Calculator,
 aggregate_table:Layers,join_tables:Merge,write_result:FileDown,export_result:FileDown,create_spreadsheet:FileSpreadsheet,
 reply_review:ShieldCheck,review_summary:ClipboardCheck,condition:GitBranch,ask_user:MessageSquare,
 finish:CheckCircle2,saved_workflow:Workflow,text_transform:Type,text_statistics:Type,
 date_transform:CalendarDays,read_web:Globe,compare_tables:Layers,edit_spreadsheet:FileSpreadsheet,
 create_document:FilePenLine,edit_document:FilePenLine,format_document:FileText,fill_template:FilePenLine,
 convert_format:FileDown,proofread_document:FileCheck,translate_document:Languages,compare_documents:Layers,
 verify_content_requirements:ShieldCheck,review_document:FileCheck,validate_document_format:FileCheck,
 check_document_links:Link,detect_language:Languages,document_visual_qa:Image,classify_document:Layers,
 verify_table_calculations:Calculator,split_pdf:Scissors,merge_documents:Merge,compress_pdf:Archive,
 render_document_pages:Image,annotate_document:FilePenLine,scalar_calculate:Calculator,json_transform:Braces,
 list_transform:Layers,url_transform:Link,base64_codec:Braces,text_hash:ShieldCheck,
 'email.list':Mail,'email.send':Mail,
};
export function WorkflowToolIcon({tool,size=18}:{tool:string;size?:number}){
 if(tool.startsWith('email.'))return <img src="/service-icons/gmail.png" width={size} height={size} alt="" aria-hidden="true" className="gmail-logo"/>;
 const Icon=icons[tool]||(tool.startsWith('calendar.')?CalendarDays:undefined)||(tool.startsWith('http')?Globe:tool.startsWith('database')?Database:Workflow);
 return <Icon size={size} aria-hidden="true"/>;
}

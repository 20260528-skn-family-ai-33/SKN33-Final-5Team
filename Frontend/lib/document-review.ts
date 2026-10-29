import type {DocumentAnalysis,AnalysisBlock} from '../server/document-analysis-contract';
export type DocumentReview={contract_version:'a4b.document_review.v1';revision:number;status:'pending'|'in_progress'|'deferred'|'completed';
 source_sha256:string;analysis_sha256:string;decisions:Record<string,{decision:'adopt'|'keep'|'hold';note:string;decided_at:string}>;
 note:string;completed_at:string|null;updated_at:string|null};
export type ReviewGroup={id:string;scope:{kind:'document'|'unit'|'image';unit_id?:string;object_id?:string};engine:string;phase:string;
 native_location:Record<string,unknown>;base_block_ids:string[];candidate_block_ids:string[];decision:'pending'|'adopt'|'keep'|'hold'};
export type ReviewableAsset={id:string;name:string;format:string;text:string;parser_status?:string;version?:number;
 parser_metadata:{document_analysis:DocumentAnalysis;document_review?:DocumentReview;analysis_artifact_directory?:string};[key:string]:any};
export type DocumentReviewView={asset:ReviewableAsset;review:DocumentReview;groups:ReviewGroup[];
 history:{revision:number;action:string;group_id?:string;decision?:string;note:string;at:string}[]};
export function blocksFor(view:DocumentReviewView,ids:string[]):AnalysisBlock[]{
 const lookup=new Map(view.asset.parser_metadata.document_analysis.blocks.map(block=>[block.id,block]));
 return ids.flatMap(id=>lookup.has(id)?[lookup.get(id)!]:[]);
}

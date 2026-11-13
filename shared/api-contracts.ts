// Data shapes exchanged between the API server (Backend/api) and the screens (Frontend).
// Type-only and moved verbatim from the server module noted above each type; those modules re-export them.
import type {Flow} from './workflow';

// from Backend/api/src/contracts.ts
export type Provider='openai'|'slack'|'bearer'|'basic'|'header'|'jwt';
// from Backend/api/src/contracts.ts
export type CredentialPublic={id:string;name:string;provider:Provider;service?:string;authType?:'oauth2';accountLabel?:string;createdAt:string;updatedAt:string};
// from Backend/api/src/assistant.ts
export type ConversationTurn={role:"user"|"assistant";content:string};
// from Backend/api/src/assistant.ts
export type AgentRun={role:'analysis'|'design'|'review'|'error';status:'completed'|'needs_input'|'blocked';summary:string;attempt:number};
// from Backend/api/src/intent-review.ts
export type RequirementReview={id:string;description:string;evidence:string;status:'mapped'|'needs_input'|'unsupported';nodeIds:string[]};
// from Backend/api/src/assistant.ts
export type AssistantResult={orchestration?:{engine:"langgraph";status:"waiting_for_answer"|"completed"};agentRuns?:AgentRun[];review?:{approved:boolean;issues:{nodeIds:string[];message:string}[]};workflow?:Flow;summary:string;checklist:string[];questions?:{question:string;choices:string[]}[];requirements?:RequirementReview[]};
// from Backend/api/src/agents.ts
export type ErrorAnalysis={summary:string;causes:{nodeId:string;message:string;confidence:'observed'|'hypothesis'}[];suggestions:{nodeId:string;action:string}[];agentRuns:AgentRun[]};
// from Backend/api/src/document-analysis-contract.ts
export type AnalysisBlock={id:string;parent_object_id:string|null;unit_id:string|null;content_types:string[];
 native_location:Record<string,unknown>;text:string;engine:string;selected?:boolean;status:string;
 bbox:null|{values:number[];coordinate_system:string};data:Record<string,unknown>;phase?:string;artifact?:string|null;parser_version?:string};
// from Backend/api/src/document-analysis-contract.ts
export type DocumentAnalysis={contract_version:'a4b.document_analysis.v1';source_sha256:string;source_format:string;
 primary_processor:string;status:'succeeded'|'partial';review_required:boolean;review_reasons:string[];
 units:{id:string;native_location:Record<string,unknown>}[];blocks:AnalysisBlock[];
 routes:{id:string;phase:string;processor:string;status:string;elapsed_seconds:number}[];
 coverage:Record<string,unknown>;warnings:string[];conversions?:{artifact:string}[]};

/** Inspect the pinned graph, including saved graph nodes, without loading credentials. */
export function builderNeedsModel(config:any,dependencies:Record<string,any>={}):boolean{
 return !config?.workflow||config.workflow.nodes.some((n:any)=>['search_document','extract_information','compose_answer','reply_review','agent_node'].includes(n.tool)||n.tool==='condition'&&n.settings.condition_operator==='semantic'||n.tool==='ask_user'&&!!n.settings.instructions||n.tool==='saved_workflow'&&!!dependencies[n.id]&&builderNeedsModel(dependencies[n.id].config,dependencies[n.id].workflow_dependencies||{}));
}

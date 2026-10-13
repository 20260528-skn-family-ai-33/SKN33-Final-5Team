/** Keep the exact selected version visible while the library refreshes its latest versions. */
export function nodeSelectionPool<T extends {ref:string}>(available:readonly T[], selectedSnapshots:readonly T[], chosenRefs:readonly string[]):T[]{
 const chosen=new Set(chosenRefs);
 const versions=new Map(available.map(node=>[node.ref,node]));
 for(const node of selectedSnapshots)if(chosen.has(node.ref))versions.set(node.ref,node);
 return [...versions.values()];
}

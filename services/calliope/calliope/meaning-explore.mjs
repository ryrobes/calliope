// Web exploration uses the same evidence graph, without inheriting canvas geometry.
const lineageLabels = new Set(['feeds', 'measured by', 'used by']);
export function neighborhood(graph, id) {
  const byId = new Map(graph.nodes.map(n => [n.id, n]));
  const groups = {inputs: [], consumers: [], context: []};
  for (const edge of graph.edges) {
    if (edge.from !== id && edge.to !== id) continue;
    const incoming = edge.to === id, other = byId.get(incoming ? edge.from : edge.to);
    if (!other) continue;
    const direct = other.kind !== 'area' && byId.get(id)?.kind !== 'area'
      && !['association', 'unverified', 'implied', 'contradicted'].includes(edge.state)
      && lineageLabels.has(edge.label);
    groups[direct ? incoming ? 'inputs' : 'consumers' : 'context'].push({node: other, edge, incoming});
  }
  for (const entries of Object.values(groups)) entries.sort((a,b) => a.node.title.localeCompare(b.node.title) || a.edge.id.localeCompare(b.edge.id));
  return groups;
}
export function collection(graph, {kind='', query='', lens='all', selected=''}={}) {
  const connected = new Set(graph.edges.filter(e => e.from === selected || e.to === selected).flatMap(e => [e.from, e.to]));
  const q = query.trim().toLocaleLowerCase();
  return graph.nodes.filter(n => n.kind !== 'area' && (!kind || kind === n.kind)
    && (lens !== 'review' || n.review?.length || ['idea','breakdown'].includes(n.kind))
    && (lens !== 'connected' || connected.has(n.id))
    && [n.title,n.description,n.source,n.subtitle,n.formula,n.contract?.window].join(' ').toLocaleLowerCase().includes(q))
    .sort((a,b) => a.title.localeCompare(b.title) || a.id.localeCompare(b.id));
}
export function connectionsCount(entries) {return new Set(entries.map(e => e.node.id)).size;}

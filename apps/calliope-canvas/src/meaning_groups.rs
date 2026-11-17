// Collapsed categories retain all identities and aggregate real edges only.
fn grouped_graph(
    nodes: &[Value],
    edges: &[Value],
    visible: &HashSet<String>,
) -> (Vec<Value>, Vec<Value>) {
    let mut output: Vec<Value> = nodes
        .iter()
        .filter(|n| visible.contains(&text(n, "id")))
        .cloned()
        .collect();
    let mut groups: std::collections::BTreeMap<String, Vec<String>> =
        std::collections::BTreeMap::new();
    let mut mapping = HashMap::new();
    for n in nodes.iter().filter(|n| !visible.contains(&text(n, "id"))) {
        let kind = text(n, "kind");
        let id = text(n, "id");
        mapping.insert(id.clone(), format!("group:{kind}"));
        groups.entry(kind).or_default().push(id);
    }
    for (kind, members) in groups {
        output.push(
            json!({"id":format!("group:{kind}"),"kind":"group","group_kind":kind,
            "title":kind_label(&kind),"members":members,"status":"collapsed",
            "description":format!("{} objects. Open to expand this entire group.",members.len()),
            "subtitle":format!("{} collapsed objects",members.len())}),
        );
    }
    let mut result = vec![];
    let mut aggregates: std::collections::BTreeMap<String, Value> =
        std::collections::BTreeMap::new();
    for e in edges {
        let a = text(e, "from");
        let b = text(e, "to");
        let from = mapping.get(&a).unwrap_or(&a);
        let to = mapping.get(&b).unwrap_or(&b);
        if from == to {
            continue;
        }
        if from == &a && to == &b {
            result.push(e.clone());
            continue;
        }
        let key = format!(
            "group-edge:{from}|{to}|{}|{}",
            text(e, "label"),
            text(e, "state")
        );
        let item=aggregates.entry(key.clone()).or_insert_with(||json!({"id":key,"from":from,"to":to,"label":e["label"],"state":e["state"],"grouped":true,"count":0}));
        item["count"] = json!(item["count"].as_u64().unwrap_or(0) + 1);
    }
    for (_, mut e) in aggregates {
        e["label"] = json!(format!("{} · {}", text(&e, "label"), e["count"]));
        result.push(e);
    }
    (output, result)
}
impl Meaning {
    fn display_nodes(&self) -> Vec<Value> {
        grouped_graph(
            &items(&self.graph, "nodes"),
            &items(&self.graph, "edges"),
            &self.visible,
        )
        .0
    }
    fn display_edges(&self) -> Vec<Value> {
        grouped_graph(
            &items(&self.graph, "nodes"),
            &items(&self.graph, "edges"),
            &self.visible,
        )
        .1
    }
    fn display_visible(&self) -> HashSet<String> {
        self.display_nodes().iter().map(|n| text(n, "id")).collect()
    }
    fn expand_group(&mut self, id: &str, cx: &mut Context<Self>) {
        if let Some(group) = self.display_nodes().iter().find(|n| n["id"] == id) {
            for member in items(group, "members") {
                if let Some(member) = member.as_str() {
                    self.visible.insert(member.to_owned());
                }
            }
        }
        self.prepare_layout();
        self.remember_view();
        self.fit_pending = true;
        cx.notify();
    }
}
#[cfg(test)]
mod group_tests {
    use super::*;
    #[::core::prelude::v1::test]
    fn collapse_preserves_counts_and_directed_relationships() {
        let nodes = vec![
            json!({"id":"a","kind":"area"}),
            json!({"id":"m1","kind":"metric"}),
            json!({"id":"m2","kind":"metric"}),
        ];
        let edges = vec![
            json!({"id":"e1","from":"a","to":"m1","label":"measured by","state":"declared"}),
            json!({"id":"e2","from":"a","to":"m2","label":"measured by","state":"declared"}),
        ];
        let (n, e) = grouped_graph(&nodes, &edges, &HashSet::from(["a".to_owned()]));
        assert_eq!(n.len(), 2);
        assert_eq!(n[1]["members"].as_array().unwrap().len(), 2);
        assert_eq!(e[0]["count"], 2);
        let (n, e) = grouped_graph(
            &nodes,
            &edges,
            &nodes.iter().map(|n| text(n, "id")).collect(),
        );
        assert_eq!(n.len(), 3);
        assert_eq!(e, edges);
    }
}

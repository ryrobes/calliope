//! Record drafts and navigation are client state, independent of SQL execution.
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::BTreeMap;

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct RecordDraft {
    pub version: Value,
    pub path: String,
    pub id: Value,
    pub value: Value,
    pub base: Value,
    pub location: String,
    pub label: String,
}
impl RecordDraft {
    pub fn dirty(&self) -> bool {
        self.value != self.base
    }
}
#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Visit {
    pub snapshot: Value,
    pub model: Value,
    pub layout: Value,
    pub fetched_at: u64,
}
#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Workspace {
    pub records: BTreeMap<String, RecordDraft>,
    pub visits: BTreeMap<String, Visit>,
    pub history: Vec<Visit>,
    pub layout: Value,
    pub fetched_at: u64,
}
pub fn model(snapshot: &Value) -> &Value {
    &snapshot["messages"][2]["updateDataModel"]["value"]
}
pub fn forms(snapshot: &Value) -> impl Iterator<Item = &Value> {
    snapshot["forms"].as_array().into_iter().flatten()
}
fn record_key(version: &Value, path: &str, id: &Value) -> String {
    json!([version, path, id]).to_string()
}
pub fn location(snapshot: &Value, data: &Value) -> String {
    let paths: Vec<_> = snapshot["navigation"]["paths"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(|p| (p, model(snapshot).pointer(p).cloned().unwrap_or_default()))
        .collect();
    let ids: Vec<_> = forms(snapshot)
        .map(|f| {
            let p = f["path"].as_str().unwrap_or("");
            (
                p,
                data.pointer(p)
                    .map(|v| &v[f["key"].as_str().unwrap_or("id")])
                    .cloned()
                    .unwrap_or_default(),
            )
        })
        .collect();
    json!([snapshot["version"], paths, ids]).to_string()
}
impl Workspace {
    pub fn current(&self, snapshot: &Value, data: &Value) -> Visit {
        let mut data = data.clone();
        for p in snapshot["navigation"]["paths"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
        {
            if let (Some(to), Some(from)) = (data.pointer_mut(p), model(snapshot).pointer(p)) {
                *to = from.clone();
            }
        }
        Visit {
            snapshot: snapshot.clone(),
            model: data,
            layout: self.layout.clone(),
            fetched_at: self.fetched_at,
        }
    }
    pub fn remember(&mut self, snapshot: &Value, data: &Value) {
        if snapshot.is_null() {
            return;
        }
        let place = location(snapshot, data);
        for form in forms(snapshot) {
            let path = form["path"].as_str().unwrap_or("");
            let Some(value) = data.pointer(path).filter(|v| v.is_object()) else {
                continue;
            };
            let id = &value[form["key"].as_str().unwrap_or("id")];
            if id.is_null() || id.as_str() == Some("") {
                continue;
            }
            let key = record_key(&snapshot["version"], path, id);
            let base = form["source"]
                .as_str()
                .and_then(|p| model(snapshot).pointer(p))
                .cloned()
                .unwrap_or_else(|| {
                    model(snapshot)
                        .pointer(path)
                        .cloned()
                        .unwrap_or_else(|| value.clone())
                });
            let old = self.records.get(&key);
            // Keep the original baseline (and record version) while edits exist.
            let baseline = old
                .filter(|r| r.dirty())
                .map(|r| r.base.clone())
                .unwrap_or(base);
            let record_place = old
                .filter(|r| r.value == *value)
                .map(|r| r.location.clone())
                .unwrap_or_else(|| place.clone());
            self.records.insert(
                key,
                RecordDraft {
                    version: snapshot["version"].clone(),
                    path: path.into(),
                    id: id.clone(),
                    value: value.clone(),
                    base: baseline,
                    location: record_place,
                    label: record_label(snapshot, data, id, value),
                },
            );
        }
        self.visits
            .insert(place.clone(), self.current(snapshot, data));
        // Bound clean view caches, but never evict a view needed by unfinished work.
        while self.visits.len() > 24 {
            let candidate = self
                .visits
                .iter()
                .filter(|(k, _)| {
                    **k != place
                        && !self
                            .records
                            .values()
                            .any(|r| r.dirty() && r.location == **k)
                })
                .min_by_key(|(_, v)| v.fetched_at)
                .map(|(k, _)| k.clone());
            let Some(key) = candidate else { break };
            self.visits.remove(&key);
        }
    }
    pub fn hints(&self, version: &Value) -> Value {
        json!(
            self.records
                .values()
                .filter(|r| r.dirty() && r.version == *version)
                .map(|r| json!({"path":r.path,"value":r.value}))
                .collect::<Vec<_>>()
        )
    }
    pub fn accepted(&mut self, before: &Value, after: &Value, prior: &Value) {
        // Reset only the record involved in this action, never other records' drafts.
        for path in after["resets"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
        {
            for (snapshot, data) in [(before, prior), (after, model(after))] {
                if let Some(form) = forms(snapshot).find(|f| f["path"] == path) {
                    if let Some(value) = data.pointer(path) {
                        let key = record_key(
                            &snapshot["version"],
                            path,
                            &value[form["key"].as_str().unwrap_or("id")],
                        );
                        if std::ptr::eq(snapshot, before) {
                            if let Some(r) = self.records.get_mut(&key) {
                                r.value = r.base.clone();
                            }
                        } else {
                            self.records.remove(&key);
                        }
                    }
                }
            }
        }
    }
    pub fn push(&mut self, visit: Visit) {
        self.history.push(visit);
        if self.history.len() > 24 {
            self.history.remove(0);
        }
    }
    pub fn restore(&self, visit: &Visit) -> Value {
        let mut snapshot = visit.snapshot.clone();
        let mut data = visit.model.clone();
        // History restores the latest draft, not the historical edits from an old visit.
        for form in forms(&snapshot) {
            let path = form["path"].as_str().unwrap_or("");
            let id = data
                .pointer(path)
                .map(|v| &v[form["key"].as_str().unwrap_or("id")])
                .cloned()
                .unwrap_or_default();
            if let Some(record) = self
                .records
                .get(&record_key(&snapshot["version"], path, &id))
            {
                if let Some(value) = data.pointer_mut(path) {
                    *value = record.value.clone();
                }
            }
        }
        snapshot["messages"][2]["updateDataModel"]["value"] = data;
        // A cached visit never replays an old command/reset receipt.
        snapshot["resets"] = json!([]);
        snapshot["receipt"] = Value::Null;
        snapshot
    }
    pub fn restore_local(&self, snapshot: &mut Value) {
        let mut local = model(snapshot).clone();
        for form in forms(snapshot) {
            let path = form["path"].as_str().unwrap_or("");
            let id = local
                .pointer(path)
                .map(|v| &v[form["key"].as_str().unwrap_or("id")])
                .cloned()
                .unwrap_or_default();
            if let Some(record) = self
                .records
                .get(&record_key(&snapshot["version"], path, &id))
            {
                if let Some(value) = local.pointer_mut(path) {
                    *value = record.value.clone();
                }
            }
        }
        crate::app_drafts::restore_staged(snapshot, &local);
    }
    pub fn cached_action(&self, snapshot: &Value, data: &Value, action: &Value) -> Option<Visit> {
        let event = action["name"].as_str()?;
        let assignments = snapshot["navigation"]["events"][event].as_object()?;
        if assignments.is_empty() {
            return None;
        }
        let mut scope = data.clone();
        scope["context"] = action["context"].clone();
        fn resolve(v: &Value, scope: &Value) -> Option<Value> {
            if let Some(p) = v["path"].as_str() {
                return scope.pointer(p).cloned();
            }
            if let Some(a) = v.as_array() {
                return a
                    .iter()
                    .map(|v| resolve(v, scope))
                    .collect::<Option<Vec<_>>>()
                    .map(Value::Array);
            }
            Some(v.clone())
        }
        let changes: Vec<_> = assignments
            .iter()
            .map(|(p, v)| resolve(v, &scope).map(|v| (p, v)))
            .collect::<Option<_>>()?;
        self.visits
            .values()
            .filter(|v| v.snapshot["version"] == snapshot["version"])
            .filter(|v| {
                changes
                    .iter()
                    .all(|(p, val)| v.model.pointer(p).is_some_and(|x| x == val))
            })
            .filter(|v| {
                snapshot["navigation"]["paths"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .all(|p| {
                        changes.iter().any(|(changed, _)| changed.as_str() == p)
                            || v.model.pointer(p) == data.pointer(p)
                    })
            })
            .max_by_key(|v| v.fetched_at)
            .cloned()
    }
    pub fn dirty(&self) -> Vec<Value> {
        self.records
            .iter()
            .filter(|(_, r)| r.dirty())
            .map(|(key, r)| json!({"key":key,"record":r.id,"form":r.path,"location":r.location,"label":if r.label.is_empty(){r.id.as_str().map(str::to_owned).unwrap_or_else(||r.id.to_string())}else{r.label.clone()}}))
            .collect()
    }
}

fn record_label(snapshot: &Value, data: &Value, id: &Value, value: &Value) -> String {
    fn resolve<'a>(v: &'a Value, data: &'a Value) -> &'a Value {
        v["path"].as_str().and_then(|p|data.pointer(p)).unwrap_or(v)
    }
    if let Some(nodes)=snapshot["messages"][1]["updateComponents"]["components"].as_array() {
        for node in nodes {
            if node["component"]=="CRecordItem" && resolve(&node["recordId"],data)==id {
                if let Some(label)=resolve(&node["label"],data).as_str().filter(|v|!v.is_empty()){return label.into();}
            }
        }
    }
    for key in ["name","title","customer_name","customer","reference"] {
        if let Some(label)=value[key].as_str().filter(|v|!v.is_empty()){return label.into();}
    }
    id.as_str().map(str::to_owned).unwrap_or_else(||id.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn page(id: &str, notes: &str) -> Value {
        json!({"version":1,"forms":[{"path":"/state/form","source":"/record","key":"id"}],"navigation":{"paths":["/state/page","/state/selected"]},"messages":[{},{},{"updateDataModel":{"value":{"state":{"page":"visit","selected":id,"form":{"id":id,"notes":notes}},"record":{"id":id,"notes":""}}}}]})
    }
    #[test]
    fn records_survive_a_b_a_and_history_uses_latest_edits() {
        let a = page("A", "");
        let mut edited = model(&a).clone();
        edited["state"]["form"]["notes"] = json!("A draft");
        let mut ws = Workspace::default();
        ws.remember(&a, &edited);
        let old = ws.current(&a, &edited);
        let b = page("B", "B draft");
        ws.remember(&b, model(&b));
        edited["state"]["form"]["notes"] = json!("A latest");
        ws.remember(&a, &edited);
        assert_eq!(
            model(&ws.restore(&old))["state"]["form"]["notes"],
            "A latest"
        );
        assert_eq!(ws.dirty().len(), 2);
        let reboot: Workspace = serde_json::from_slice(&serde_json::to_vec(&ws).unwrap()).unwrap();
        assert_eq!(reboot.hints(&json!(1)).as_array().unwrap().len(), 2);
        assert_eq!(reboot.hints(&json!(2)), json!([]));
    }
    #[test]
    fn draft_names_follow_bound_record_labels() {
        let mut a=page("A","");
        a["messages"][1]=json!({"updateComponents":{"components":[{"component":"CRecordItem","recordId":"A","label":{"path":"/record/name"}}]}});
        a["messages"][2]["updateDataModel"]["value"]["record"]["name"]=json!("Cedar House · inspection");
        let mut data=model(&a).clone();
        data["state"]["form"]["notes"]=json!("Check the drain");
        let mut ws=Workspace::default();ws.remember(&a,&data);
        assert_eq!(ws.dirty()[0]["label"],"Cedar House · inspection");
        assert_eq!(ws.dirty()[0]["record"],"A");
    }
    #[test]
    fn discard_does_not_resurrect_from_history_or_erase_other_record() {
        let a = page("A", "unsaved");
        let b = page("B", "keep");
        let mut ws = Workspace::default();
        ws.remember(&a, model(&a));
        let old = ws.current(&a, model(&a));
        ws.remember(&b, model(&b));
        let mut reset = page("A", "");
        reset["resets"] = json!(["/state/form"]);
        ws.accepted(&a, &reset, model(&a));
        ws.remember(&reset, model(&reset));
        assert_eq!(ws.dirty().len(), 1);
        assert_eq!(model(&ws.restore(&old))["state"]["form"]["notes"], "");
    }
}

//! Host-owned asynchronous derived atoms. No renderer callbacks or inference at paint time.
use serde_json::{Value, json};
use std::collections::BTreeMap;

struct Entry {
    definition: Value,
    key: Option<String>,
    payload: Value,
    due: u64,
    started: u64,
    inflight: Option<u64>,
    ticket: u64,
    held: bool,
    state: Value,
}
#[derive(Default)]
pub struct Reactive {
    document: String,
    serial: u64,
    entries: BTreeMap<String, Entry>,
}
pub struct Request {
    pub id: String,
    pub ticket: u64,
    pub body: Value,
}
impl Reactive {
    pub fn clear(&mut self) {
        self.document.clear();
        self.entries.clear();
    }
    pub fn install(&mut self, snapshot: &Value) {
        let mut document = json!([snapshot["view"], snapshot["selected"], snapshot["reactive"]]);
        document.sort_all_objects();
        let key = document.to_string();
        if self.document == key {
            return;
        }
        self.document = key;
        self.entries.clear();
        for r in snapshot["reactive"].as_array().into_iter().flatten() {
            let id = r["id"].as_str().unwrap_or_default().to_owned();
            let label = r["initial"].as_str().unwrap_or_default();
            self.serial += 1;
            self.entries.insert(id, Entry { definition:r.clone(), key:None, payload:Value::Null, due:0, started:0,
                inflight:None, ticket:self.serial, held:false,
                state:json!({"label":label,"value":r["variants"][label]["value"],"status":"idle","message":"Waiting for context"}) });
        }
    }
    pub fn observe(&mut self, data: &Value, now: u64) -> bool {
        let mut changed = false;
        for e in self.entries.values_mut() {
            let mut payload = serde_json::Map::new();
            for (k, p) in e.definition["watch"].as_object().into_iter().flatten() {
                payload.insert(
                    k.clone(),
                    data.pointer(p.as_str().unwrap_or(""))
                        .cloned()
                        .unwrap_or(Value::Null),
                );
            }
            let mut payload = Value::Object(payload);
            payload.sort_all_objects();
            let key = payload.to_string();
            if e.key.as_ref() != Some(&key) {
                e.key = Some(key);
                e.payload = payload;
                self.serial += 1;
                e.ticket = self.serial;
                e.due = now + e.definition["debounceMs"].as_u64().unwrap_or(350);
                e.state["status"] = if e.held { "held" } else { "waiting" }.into();
                e.state["message"] = if e.held {
                    "Held · context changed"
                } else {
                    "Context changed · keeping current workspace"
                }
                .into();
                changed = true;
            }
        }
        changed
    }
    pub fn poll(&mut self, now: u64) -> Vec<Request> {
        let mut requests = Vec::new();
        for (id, e) in &mut self.entries {
            if !e.held && e.inflight.is_none() && e.state["status"] == "waiting" && now >= e.due {
                e.inflight = Some(e.ticket);
                e.started = now;
                e.state["status"] = "pending".into();
                requests.push(Request {
                    id: id.clone(),
                    ticket: e.ticket,
                    body: json!({"state":e.payload,"tree":e.definition["tree"],"max_steps":4}),
                });
            }
        }
        requests
    }
    pub fn pending(&mut self, now: u64) -> bool {
        let mut changed = false;
        for e in self.entries.values_mut() {
            if e.state["status"] == "pending"
                && now.saturating_sub(e.started) >= 250
                && e.state["message"] != "◌ Considering this context…"
            {
                e.state["message"] = "◌ Considering this context…".into();
                changed = true;
            }
        }
        changed
    }
    pub fn receive(&mut self, id: &str, ticket: u64, result: Value) -> bool {
        let Some(e) = self.entries.get_mut(id) else {
            return false;
        };
        if e.inflight != Some(ticket) {
            return false;
        }
        e.inflight = None;
        if e.ticket != ticket || e.held {
            return false;
        }
        let decision = &result["decision"];
        let label = decision["label"].as_str().unwrap_or("");
        if decision["status"] != "complete" || e.definition["variants"].get(label).is_none() {
            e.state["status"] = "error".into();
            e.state["message"] =
                "Decision unavailable · previous workspace kept. Reconsider to retry.".into();
            e.state["receipt"] = result;
        } else {
            e.state = json!({"label":label,"value":e.definition["variants"][label]["value"],"status":"ready",
                "message":format!("{} · {} ms · {} decision{}",label,result["elapsed_ms"],decision["steps"],if decision["steps"]==1 {""} else {"s"}),"receipt":result});
        }
        true
    }
    pub fn action(&mut self, name: &str, id: &str) -> bool {
        if !matches!(name, "reactive_hold" | "reactive_retry") {
            return false;
        }
        let Some(e) = self.entries.get_mut(id) else {
            return false;
        };
        if name == "reactive_hold" {
            e.held = !e.held;
        }
        self.serial += 1;
        e.ticket = self.serial;
        e.due = 0;
        e.state["status"] = if e.held { "held" } else { "waiting" }.into();
        e.state["message"] = if e.held {
            "Held · this workspace stays put"
        } else {
            "Reconsidering context"
        }
        .into();
        true
    }
    pub fn force(&mut self, id: &str, label: &str) -> bool {
        let Some(e) = self.entries.get_mut(id) else {
            return false;
        };
        let Some(v) = e.definition["variants"].get(label) else {
            return false;
        };
        self.serial += 1;
        e.ticket = self.serial;
        e.held = true;
        e.state = json!({"label":label,"value":v["value"],"status":"held","message":"Studio forced outcome · inference bypassed","receipt":{"source":"studio-forced","label":label}});
        true
    }
    pub fn states(&self) -> Value {
        Value::Object(
            self.entries
                .iter()
                .map(|(id, e)| (id.clone(), e.state.clone()))
                .collect(),
        )
    }
    pub fn busy(&self) -> bool {
        self.entries
            .values()
            .any(|e| matches!(e.state["status"].as_str(), Some("waiting" | "pending")))
    }
    pub fn components(&self, base: &Value) -> Value {
        let mut nodes = base.as_array().cloned().unwrap_or_default();
        for e in self.entries.values() {
            let label = e.state["label"].as_str().unwrap_or_default();
            let v = &e.definition["variants"][label];
            for (id, patch) in v["patches"].as_object().into_iter().flatten() {
                if let Some(node) = nodes.iter_mut().find(|n| n["id"] == *id) {
                    for (key, value) in patch.as_object().into_iter().flatten() {
                        node[key] = value.clone();
                    }
                }
            }
            if let Some(target) = e.definition["target"].as_str() {
                if let Some(node) = nodes.iter_mut().find(|n| n["id"] == target) {
                    node["children"] = v["root"]
                        .as_str()
                        .map(|root| json!([root]))
                        .unwrap_or(json!([]));
                }
            }
            nodes.extend(v["components"].as_array().into_iter().flatten().cloned());
        }
        Value::Array(nodes)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn document() -> Value {
        json!({"view":"studio","selected":"a","reactive":[{"id":"x","watch":{"notes":"/draft/notes"},"debounceMs":100,"initial":"a","tree":{"kind":"leaf","label":"b"},"variants":{"a":{"value":{"text":"A"}},"b":{"value":{"text":"B"}}}}]})
    }
    fn result(label: &str) -> Value {
        json!({"decision":{"status":"complete","label":label,"steps":1},"elapsed_ms":12})
    }
    #[test]
    fn debounce_stale_and_precise_watch() {
        let mut r = Reactive::default();
        r.install(&document());
        r.observe(&json!({"draft":{"notes":"one"}}), 0);
        assert!(r.poll(99).is_empty());
        let first = r.poll(100).remove(0);
        r.observe(&json!({"draft":{"notes":"two"}}), 150);
        assert!(r.poll(250).is_empty());
        assert!(!r.receive("x", first.ticket, result("b")));
        let next = r.poll(251).remove(0);
        assert_eq!(next.body["state"]["notes"], "two");
        assert!(r.receive("x", next.ticket, result("b")));
        assert!(!r.observe(
            &json!({"draft":{"notes":"two","other":"changed"},"resources":{"noise":true}}),
            300
        ));
        assert_eq!(r.states()["x"]["value"]["text"], "B");
        assert!(r.poll(500).is_empty());
    }
    #[test]
    fn object_key_order_is_not_a_dependency_change() {
        let mut r = Reactive::default();
        let mut d = document();
        d["reactive"][0]["watch"] = json!({"job":"/job"});
        r.install(&d);
        let first: Value = serde_json::from_str(r#"{"job":{"id":1,"notes":"same"}}"#).unwrap();
        let reordered: Value = serde_json::from_str(r#"{"job":{"notes":"same","id":1}}"#).unwrap();
        assert!(r.observe(&first, 0));
        assert!(!r.observe(&reordered, 100));
        d.sort_all_objects();
        r.install(&d);
        assert!(!r.observe(&reordered, 200));
    }
    #[test]
    fn navigation_hold_failure_and_retry() {
        let mut r = Reactive::default();
        r.install(&document());
        r.observe(&json!({"draft":{"notes":"a"}}), 0);
        let first = r.poll(100).remove(0);
        r.action("reactive_hold", "x");
        assert!(!r.receive("x", first.ticket, result("b")));
        assert!(r.poll(200).is_empty());
        r.action("reactive_hold", "x");
        let second = r.poll(200).remove(0);
        assert!(r.receive("x", second.ticket, Value::Null));
        assert_eq!(r.states()["x"]["label"], "a");
        assert!(r.poll(9999).is_empty());
        r.action("reactive_retry", "x");
        let old = r.poll(10000).remove(0);
        let mut d = document();
        d["selected"] = "b".into();
        r.install(&d);
        r.observe(&json!({"draft":{"notes":"a"}}), 10001);
        let new = r.poll(10102).remove(0);
        assert!(!r.receive("x", old.ticket, result("b")));
        assert!(r.receive("x", new.ticket, result("b")));
    }
    #[test]
    fn forced_outcomes_are_held_and_marked() {
        let mut r = Reactive::default();
        r.install(&document());
        assert!(!r.force("x", "missing"));
        assert!(r.force("x", "b"));
        r.observe(&json!({"draft":{"notes":"new"}}), 0);
        assert!(r.poll(10000).is_empty());
        assert_eq!(r.states()["x"]["receipt"]["source"], "studio-forced");
    }
}

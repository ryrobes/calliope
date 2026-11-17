//! Semantic testing through the real renderer's bound state and activation path.
use crate::app::GpuiApp;
use gpui::{Context, Window};
use serde_json::{Value, json};
impl GpuiApp {
    pub fn context_revision(&self)->u64 {
        use std::hash::{Hash,Hasher};
        self.rendered_nodes.borrow().iter().fold(self.data_revision(),|key,id|{let mut h=std::collections::hash_map::DefaultHasher::new();id.hash(&mut h);key^h.finish()})
    }
    pub fn probe_render_status(&self, cx: &gpui::App) -> Value {
        let charts: serde_json::Map<String, Value> = self
            .chart_views
            .borrow()
            .iter()
            .map(|(id, view)| (id.clone(), view.read(cx).probe_status()))
            .collect();
        json!({"focused_input":self.active_input,"field_errors":self.field_errors,"input_kind":self.last_input_kind,"inputs":self.input_bounds.iter().map(|(id,b)|(id.clone(),json!({"x":f32::from(b.origin.x),"y":f32::from(b.origin.y),"width":f32::from(b.size.width),"height":f32::from(b.size.height)}))).collect::<serde_json::Map<String,Value>>(),"picker":self.field_picker.as_ref().map(|p|p.id.clone()),"viewport":self.view_state(),"charts":charts,"refresh_pending":self.refresh_pending,"scroll_areas":self.scroll_handles.borrow().iter().map(|(id,h)|(id.clone(),json!({"offset_y":f32::from(h.offset().y),"height":f32::from(h.bounds().size.height),"width":f32::from(h.bounds().size.width),"x":f32::from(h.bounds().origin.x),"y":f32::from(h.bounds().origin.y)}))).collect::<serde_json::Map<String,Value>>()})
    }
    pub fn probe_snapshot(&self) -> Value {
        let Some(surface) = self.processor.model.surfaces().next() else {
            return json!({});
        };
        let data = surface.data_model.borrow();
        fn resolve(v: &Value, data: &a2ui_base::model::data_model::DataModel) -> Value {
            if let Some(path) = v
                .as_object()
                .filter(|o| o.len() == 1)
                .and_then(|o| o.get("path"))
                .and_then(Value::as_str)
            {
                return data.get(path).cloned().unwrap_or(Value::Null);
            }
            match v {
                Value::Object(o) => Value::Object(
                    o.iter()
                        .map(|(k, v)| (k.clone(), resolve(v, data)))
                        .collect(),
                ),
                Value::Array(a) => Value::Array(a.iter().map(|v| resolve(v, data)).collect()),
                _ => v.clone(),
            }
        }
        let mut nodes:Vec<Value>=surface.components.borrow().all().iter().map(|(id,n)|{let raw=Value::Object(n.properties.clone());json!({"id":id,"component":n.component_type,"props":resolve(&raw,&data),"bindings":raw,"rendered":self.rendered_nodes.borrow().contains(id)})}).collect();
        nodes.sort_by_key(|n| n["id"].as_str().unwrap_or("").to_owned());
        json!({"nodes":nodes,"data":data.get("").or_else(||data.get("/")).cloned()})
    }
    /// Validate the complete patch against one pre-edit view before changing any
    /// binding. Reactive visibility of later fields cannot cause a partial edit.
    pub fn apply_draft_changes(&mut self, operations:&[Value], window:&mut Window, cx:&mut Context<Self>)->Result<(),String>{
        if self.interaction_disabled{return Err("UI is busy".into());}
        let snapshot=self.probe_snapshot();
        let mut updates=Vec::new();
        for op in operations {
            if op["op"]!="fill" {return Err("Only draft fields can be patched".into());}
            let id=op["id"].as_str().ok_or("Missing field ID")?;
            let node=snapshot["nodes"].as_array().and_then(|a|a.iter().find(|n|n["id"]==id && n["rendered"]==true)).ok_or("Field is no longer visible")?;
            let props=&node["props"];
            if props["readOnly"]==true || props["disabled"]==true || props["visible"]==false{return Err("Field is not editable".into());}
            let path=node["bindings"]["value"]["path"].as_str().filter(|p|p.starts_with("/state/")).ok_or("Field has no draft binding")?;
            let value=&op["value"];
            let valid=match node["component"].as_str().unwrap_or("") {
                "CField"|"TextField"=>value.is_string(),
                "CheckBox"=>value.is_boolean(),
                "ChoicePicker"|"CRecordPicker"=>value.as_array().is_some_and(|a|a.iter().all(|v|props["options"].as_array().is_some_and(|opts|opts.iter().any(|o|o["value"]==*v)))),
                _=>false,
            };
            if !valid{return Err("Invalid field value".into());}
            updates.push((id.to_owned(),path.to_owned(),value.clone()));
        }
        for (id,path,value) in updates {
            self.set_data_value(&path,value.clone());
            if let Some(text)=value.as_str(){
                if let Some(state)=self.text_states.borrow().get(&id).cloned(){state.update(cx,|s,cx|s.set_value(text.to_owned(),window,cx));}
                #[cfg(feature="gpui-next")]
                if let Some(state)=self.textarea_states.borrow().get(&id).cloned(){state.update(cx,|s,cx|s.set_value(text.to_owned(),window,cx));}
            }
        }
        cx.notify();Ok(())
    }
    pub fn probe_command(&mut self, body: &Value, window: &mut Window, cx: &mut Context<Self>) -> Value {
        self.interact(body, window, cx)
    }
    /// Shared semantic activation path for host UI and structural tests.
    pub fn interact(
        &mut self,
        body: &Value,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Value {
        let id = body["id"].as_str().unwrap_or("");
        let node = self
            .processor
            .model
            .surfaces()
            .next()
            .and_then(|s| s.components.borrow().get(id).cloned());
        let Some(node) = node else {
            return json!({"error":"Unknown component ID"});
        };
        if self.interaction_disabled {
            return json!({"error":"UI is busy"});
        }
        match body["op"].as_str() {
            Some("press") if matches!(node.component_type.as_str(), "CLink" | "CRecordLink") => {
                self.emit_action(json!({"name":"open_link","context":{"componentId":id}}));
                cx.notify();json!({"queued":true})
            }
            Some("focus") => {self.focus_requested=Some(id.to_owned());cx.notify();json!({"ok":true})}
            Some("picker") => {self.open_field_picker(id,window,cx);json!({"ok":self.field_picker.is_some()})}
            Some("press") if node.component_type == "CSection" => {
                let snapshot=self.probe_snapshot();
                let props=snapshot["nodes"].as_array().and_then(|a|a.iter().find(|n|n["id"]==id)).map(|n|n["props"].clone()).unwrap_or_default();
                let key=format!("section:{id}:{}",props["summary"].as_str().unwrap_or(""));
                let value=1-self.local_tabs.get(&key).copied().unwrap_or(0).min(1);
                self.local_tabs.insert(key,value);cx.notify();json!({"ok":true,"expanded":value==1})
            }
            Some("press") if node.component_type == "CAttachments" => {
                let props = Value::Object(node.properties);
                let path = props["value"]["path"].as_str().unwrap_or("");
                let ids = if props["value"].is_array() {
                    props["value"].clone()
                } else {
                    self.data_value(path)
                };
                if let Some(id) = body["file"].as_str() {
                    if !ids.as_array().is_some_and(|a| a.iter().any(|v| v == id)) {
                        return json!({"error":"Unknown attachment"});
                    }
                    self.emit_action(json!({"name":"open_attachment","context":{"id":id}}));
                } else {
                    if props["readOnly"] == true || ids.as_array().is_some_and(|a| a.len() >= 5) {
                        return json!({"error":"Attachment list is not editable"});
                    }
                    let action = if cfg!(any(target_os = "android", target_os = "ios")) && body["camera"] == true {
                        "capture_photo"
                    } else {
                        "choose_attachment"
                    };
                    self.emit_action(json!({"name":action,"context":{"path":path}}));
                }
                cx.notify();
                json!({"queued":true})
            }
            Some("press") if node.component_type == "CRecordItem" => {
                self.activate_record(id);
                cx.notify();
                json!({"queued":true})
            }
            Some("press") if node.component_type == "Button" => {
                self.handle_activate(id);
                cx.notify();
                json!({"queued":true})
            }
            Some("fill")
                if matches!(
                    node.component_type.as_str(),
                    "CField" | "TextField" | "ChoicePicker" | "CRecordPicker" | "CheckBox"
                ) =>
            {
                let rendered = self.probe_snapshot();
                let resolved = rendered["nodes"]
                    .as_array()
                    .and_then(|a| a.iter().find(|n| n["id"] == id))
                    .map(|n| n["props"].clone())
                    .unwrap_or(Value::Null);
                let props = Value::Object(node.properties);
                if resolved["readOnly"] == true || resolved["visible"] == false {
                    return json!({"error":"Field is not editable"});
                }
                let Some(path) = props["value"]["path"].as_str() else {
                    return json!({"error":"Field has no writable binding"});
                };
                let value = body["value"].clone();
                if matches!(node.component_type.as_str(), "CField" | "TextField")
                    && !value.is_string()
                {
                    return json!({"error":"Text inputs require a string"});
                }
                if node.component_type == "CheckBox" && !value.is_boolean() {
                    return json!({"error":"Checkboxes require a boolean"});
                }
                if matches!(
                    node.component_type.as_str(),
                    "CRecordPicker" | "ChoicePicker"
                ) && (!value.is_array()
                    || !value.as_array().unwrap().iter().all(|v| {
                        resolved["options"]
                            .as_array()
                            .is_some_and(|a| a.iter().any(|o| o["value"] == *v))
                    }))
                {
                    return json!({"error":"Choose an offered value"});
                }
                self.set_data_value(path, value.clone());
                if let Some(state) = self.text_states.borrow().get(id).cloned() {
                    if let Some(text) = value.as_str() {
                        state.update(cx, |s, cx| s.set_value(text.to_string(), window, cx));
                    }
                }
                #[cfg(feature="gpui-next")]
                if let Some(state)=self.textarea_states.borrow().get(id).cloned(){if let Some(text)=value.as_str(){state.update(cx,|s,cx|s.set_value(text.to_owned(),window,cx));}}
                cx.notify();
                json!({"ok":true})
            }
            _ => json!({"error":"Use inspect, fill(editable component) or press(Button)"}),
        }
    }
}

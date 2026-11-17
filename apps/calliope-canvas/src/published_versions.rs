// The server owns definitions. Clients explicitly switch versions only when work is settled.
fn compatibility(release: &Value, catalog: &Value) -> Option<String> {
    if release.is_null() { return None; } // Older server; component validation still applies.
    if release["schema"] != "calliope.app.v1" || release["catalog_id"] != catalog["id"] {
        return Some("This app needs a different renderer. Open it in the browser or update Calliope.".into());
    }
    if release["catalog_version"].as_u64().unwrap_or(u64::MAX) > catalog["version"].as_u64().unwrap_or(0) {
        return Some(format!("This app needs component catalog v{}; this client has v{}. Open it in the browser or update Calliope.", release["catalog_version"], catalog["version"]));
    }
    if let Some(required) = release["components"].as_array() {
        let missing: Vec<_> = required.iter().filter_map(Value::as_str).filter(|name| {
            !["Button", "TextField", "ChoicePicker", "CheckBox"].contains(name) && catalog["components"].get(*name).is_none()
        }).collect();
        if !missing.is_empty() {return Some(format!("Unsupported components: {}. Open this app in the browser or update Calliope.",missing.join(", ")));}
    }
    None
}
impl PublishedApps {
    fn latest_version(&self) -> u64 {
        self.snapshot["latest_version"].as_u64().unwrap_or(0).max(
            self.apps.iter().find(|a|a["slug"]==self.slug).and_then(|a|a["latest_version"].as_u64()).unwrap_or(0))
    }
    fn update_latest(&mut self, cx: &mut Context<Self>) {
        if self.busy || self.snapshot.is_null() { return; }
        self.persist(cx);
        self.status_open = true;
        let ready = self.retry.is_none() && self.draft_error.is_none() && self.workspace.dirty().is_empty()
            && !crate::app_drafts::has_local(&self.renderer.read(cx).data_value("/"))
            && self.store.load(&self.slug).is_ok_and(|d| d.pending.is_none() && d.capture.is_none());
        if !ready {
            self.notice = "Finish or discard your drafts and resolve any pending submission before changing app versions. Your work stays on the current version.".into();
            cx.notify(); return;
        }
        let latest = self.latest_version();
        if latest > self.snapshot["version"].as_u64().unwrap_or(0) {
            // No state/draft values cross a version boundary. Keep the old surface until success.
            self.send(json!({"version":latest}), cx);
        }
        cx.notify();
    }
}
#[cfg(test)]
mod version_tests {
    use super::compatibility;
    use serde_json::{Value,json};
    #[test]
    fn incompatible_versions_explain_the_browser_escape() {
        let catalog: Value=serde_json::from_str(include_str!("../business/catalog.json")).unwrap();
        let mut release=json!({"schema":"calliope.app.v1","catalog_id":catalog["id"],"catalog_version":catalog["version"],"components":["CText"]});
        assert!(compatibility(&release,&catalog).is_none());
        release["catalog_version"]=json!(999);
        assert!(compatibility(&release,&catalog).unwrap().contains("browser"));
        release["catalog_version"]=catalog["version"].clone();
        release["components"]=json!(["FutureMap"]);
        assert!(compatibility(&release,&catalog).unwrap().contains("FutureMap"));
    }
}

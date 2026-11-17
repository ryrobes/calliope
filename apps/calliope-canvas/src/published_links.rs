fn browser_link_target(base: &str, href: &str) -> anyhow::Result<String> {
    anyhow::ensure!(!href.is_empty() && href.len()<=2048 && !href.chars().any(|c|c.is_whitespace()||c.is_control()||c=='\\'),"Link unavailable");
    let relative=href.starts_with('/') && !href.starts_with("//");
    anyhow::ensure!(relative || href.starts_with("https://"),"Links require an instance path or HTTPS URL");
    let target=if relative {reqwest::Url::parse(base)?.join(href)?} else {reqwest::Url::parse(href)?};
    anyhow::ensure!((relative && matches!(target.scheme(),"http"|"https") || target.scheme()=="https") && target.host_str().is_some() && target.username().is_empty() && target.password().is_none(),"Links require an instance path or HTTPS URL without credentials");
    Ok(target.to_string())
}
impl PublishedApps {
    fn revision_link(&mut self, changes: bool, cx: &mut Context<Self>) {
        let path=if changes {format!("/apps/{}/changes",self.slug)}else{self.snapshot["update"]["compare_url"].as_str().unwrap_or("").to_string()};
        if let Ok(url)=browser_link_target(&self.api.config.browser_url,&path) {
            self.persist(cx);
            #[cfg(any(target_os="android",target_os="ios"))]
            if let Err(error)=crate::host::open_url(&url){self.notice=error;}
            #[cfg(not(any(target_os="android",target_os="ios")))]
            cx.open_url(&url);
        }
        cx.notify();
    }
    fn open_link(&mut self, id: &Value, cx: &mut Context<Self>) {
        let surface=self.renderer.read(cx).probe_snapshot();
        let node=surface["nodes"].as_array().into_iter().flatten()
            .find(|n|n["id"]==*id && n["rendered"]==true);
        if let Some(node)=node.filter(|n|n["component"]=="CRecordLink") {
            if let Err(error)=self.open_record(node["props"]["reference"].clone(),false,cx) {
                self.notice=error.to_string();self.failed=true;cx.notify();
            }
            return;
        }
        let href=node.filter(|n|n["component"]=="CLink").and_then(|n|n["props"]["href"].as_str()).unwrap_or("");
        match crate::app_pins::reference_from_url(&self.api.config.browser_url,href) {
            Ok(Some(reference))=>{
                if let Err(error)=self.open_record(reference,false,cx){self.notice=error.to_string();cx.notify();}
                return;
            }
            Err(error)=>{self.notice=error.to_string();cx.notify();return;}
            Ok(None)=>{}
        }
        match browser_link_target(&self.api.config.browser_url,href) {
            Ok(url)=>{
                self.persist(cx);
                #[cfg(any(target_os="android", target_os="ios"))]
                if let Err(error)=crate::host::open_url(&url){self.notice=error;}
                #[cfg(not(any(target_os="android", target_os="ios")))]
                cx.open_url(&url);
            }
            Err(error)=>self.notice=error.to_string(),
        }
        cx.notify();
    }
}
#[cfg(test)]
mod link_tests {
    use super::browser_link_target;
    #[test]
    fn links_use_configured_browser_origin_without_credentials() {
        assert_eq!(browser_link_target("http://127.0.0.1:18080","/d/visits?job=HV-1001").unwrap(),"http://127.0.0.1:18080/d/visits?job=HV-1001");
        assert_eq!(browser_link_target("https://instance.test","https://docs.example.test/help").unwrap(),"https://docs.example.test/help");
        for value in ["","//evil.test","/\\evil.test","javascript:alert(1)","file:///tmp/x","http://elsewhere.test","https://name:secret@site.test","/x\ny"] {
            assert!(browser_link_target("https://instance.test",value).is_err(),"{value}");
        }
    }
}

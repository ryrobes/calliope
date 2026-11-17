use crate::model::*;
use anyhow::{Context, Result, bail};
use reqwest::{
    Url,
    blocking::{Client, Response},
    header::{CONTENT_TYPE, COOKIE, SET_COOKIE},
    redirect::Policy,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    io::{BufReader, Read},
    path::PathBuf,
    time::Duration,
};

#[derive(Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Connection {
    pub base_url: String,
    pub browser_url: String,
    pub email: String,
    pub session_cookie: String,
}

impl Default for Connection {
    fn default() -> Self {
        Self {
            base_url: "http://localhost:18080".into(),
            browser_url: "https://localhost:18443".into(),
            email: "admin@example.com".into(),
            session_cookie: String::new(),
        }
    }
}

impl Connection {
    pub fn load() -> Self {
        std::fs::read(config_path())
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or_default()
    }
    pub fn save(&self) -> Result<()> {
        private_write(&config_path(), &serde_json::to_vec_pretty(self)?)
    }
    pub fn account(&self) -> String {
        format!(
            "{}|{}",
            self.base_url.trim_end_matches('/'),
            self.email.to_lowercase()
        )
    }
}

#[derive(Clone)]
pub struct Api {
    pub config: Connection,
    client: Client,
}

impl Api {
    pub fn new(config: Connection) -> Result<Self> {
        let url = Url::parse(&config.base_url)?;
        let loopback = matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"));
        if url.scheme() != "https" && !(url.scheme() == "http" && loopback) {
            bail!("Use HTTPS, or HTTP through the local Lab tunnel.");
        }
        if !url.username().is_empty() || url.password().is_some() {
            bail!("Credentials must not be part of the server URL.");
        }
        Ok(Self {
            config,
            client: Client::builder()
                .redirect(Policy::none())
                .connect_timeout(Duration::from_secs(8))
                .timeout(Duration::from_secs(45))
                .build()?,
        })
    }

    pub fn login(mut config: Connection, password: String) -> Result<Connection> {
        let api = Self::new(config.clone())?;
        let r = api
            .client
            .post(api.url("/login")?)
            .form(&[
                ("email", config.email.as_str()),
                ("password", password.as_str()),
                ("next", "/api/calliope/sessions"),
            ])
            .send()?;
        config.session_cookie = r
            .headers()
            .get_all(SET_COOKIE)
            .iter()
            .filter_map(|v| v.to_str().ok())
            .find_map(|v| {
                v.strip_prefix("wh_session=")
                    .map(|s| s.split(';').next().unwrap_or_default().to_owned())
            })
            .context("Sign-in failed. Check your email and Calliope password.")?;
        Self::new(config.clone())?.sessions()?;
        config.save()?;
        Ok(config)
    }

    pub fn url(&self, path: &str) -> Result<Url> {
        let base = Url::parse(&self.config.base_url)?;
        let result = base.join(path)?;
        if result.origin() != base.origin() {
            bail!("Refusing to send Calliope credentials to a different origin.");
        }
        Ok(result)
    }

    pub(crate) fn request(
        &self,
        method: reqwest::Method,
        path: &str,
    ) -> Result<reqwest::blocking::RequestBuilder> {
        Ok(self
            .client
            .request(method, self.url(path)?)
            .header(COOKIE, format!("wh_session={}", self.config.session_cookie)))
    }

    pub(crate) fn json_response(r: Response) -> Result<Value> {
        let status = r.status();
        if status.as_u16() == 401 || status.is_redirection() {
            return Err(crate::app_drafts::HttpError {status:401,code:"UNAUTHORIZED".into(),message:"Your Calliope session expired. Reconnect to sign in again.".into()}.into());
        }
        let is_json = r
            .headers()
            .get(CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .is_some_and(|s| s.contains("application/json"));
        if !is_json {
            bail!(
                "Calliope returned {} instead of JSON. Check the tunnel and server URL.",
                status
            );
        }
        let value: Value = r.json()?;
        if !status.is_success() {
            return Err(crate::app_drafts::HttpError {
                status: status.as_u16(),
                code: value
                    .pointer("/error/code")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .into(),
                message: value
                    .pointer("/error/message")
                    .or_else(|| value.pointer("/error/code"))
                    .map(display)
                    .unwrap_or_else(|| format!("Calliope returned {status}")),
            }
            .into());
        }
        Ok(value)
    }

    pub fn get(&self, path: &str) -> Result<Value> {
        Self::json_response(self.request(reqwest::Method::GET, path)?.send()?)
    }

    pub fn asset(&self, path: &str, limit: u64) -> Result<Vec<u8>> {
        let response = self.request(reqwest::Method::GET, path)?.send()?;
        if !response.status().is_success() {
            bail!("Could not load appearance asset ({})", response.status());
        }
        if response
            .headers()
            .get(CONTENT_TYPE)
            .and_then(|h| h.to_str().ok())
            .is_some_and(|mime| mime.contains("text/html"))
        {
            bail!("Appearance asset returned a login page instead of content");
        }
        let mut bytes = Vec::new();
        response.take(limit + 1).read_to_end(&mut bytes)?;
        if bytes.is_empty() || bytes.len() as u64 > limit {
            bail!("Appearance asset is empty or exceeds the size limit");
        }
        Ok(bytes)
    }

    pub fn transcribe(&self, wav: Vec<u8>, seconds: f64) -> Result<String> {
        let file = reqwest::blocking::multipart::Part::bytes(wav)
            .file_name("calliope-recording.wav")
            .mime_str("audio/wav")?;
        let body = reqwest::blocking::multipart::Form::new()
            .part("file", file)
            .text("surface", "chat")
            .text("duration_seconds", seconds.to_string());
        let result = Self::json_response(
            self.request(reqwest::Method::POST, "/api/calliope/transcriptions")?
                .timeout(Duration::from_secs(180))
                .multipart(body)
                .send()?,
        )?;
        let text = string(&result, "text");
        if text.trim().is_empty() {
            bail!("No speech detected. Check your microphone and try again.");
        }
        Ok(text.trim().to_owned())
    }
    pub fn post(&self, path: &str, body: &Value) -> Result<Value> {
        Self::json_response(
            self.request(reqwest::Method::POST, path)?
                .json(body)
                .send()?,
        )
    }
    pub fn sessions(&self) -> Result<Vec<Session>> {
        Ok(serde_json::from_value(
            self.get("/api/calliope/sessions")?["sessions"].clone(),
        )?)
    }
    pub fn thread(&self, id: &str) -> Result<Thread> {
        validate_uuid(id)?;
        Ok(serde_json::from_value(
            self.get(&format!("/api/calliope/sessions/{id}"))?,
        )?)
    }
    pub fn create_thread(&self) -> Result<Session> {
        Ok(serde_json::from_value(
            self.post(
                "/api/calliope/sessions",
                &json!({"title":"Canvas workspace"}),
            )?["session"]
                .clone(),
        )?)
    }
    pub fn contents(&self, id: &str) -> Result<Value> {
        validate_uuid(id)?;
        self.get(&format!("/api/calliope/data/surfaces/{id}/contents"))
    }
    pub fn preview(&self, surface: &str, entry: &str, refresh: bool) -> Result<Value> {
        validate_uuid(surface)?;
        let mut u = self.url(&format!("/api/calliope/data/surfaces/{surface}/queries/"))?;
        u.path_segments_mut()
            .map_err(|_| anyhow::anyhow!("Invalid server URL"))?
            .pop_if_empty()
            .push(entry)
            .push("preview");
        Self::json_response(
            self.request(reqwest::Method::POST, u.path())?
                .header("x-calliope-data", "1")
                .json(&json!({"refresh":refresh}))
                .send()?,
        )
    }

    pub fn board_preview(&self, source: &crate::board::QuerySource) -> Result<Value> {
        let contents = self.contents(&source.surface_id)?;
        if contents["kind"] != "query"
            || contents["session_id"] != source.session_id
            || contents["surface_id"] != source.surface_id
        {
            bail!("This source is no longer available in the board's conversation.");
        }
        let item = contents["items"]
            .as_array()
            .filter(|items| items.len() == 1)
            .and_then(|items| items.first())
            .context("This saved query has no refresh definition.")?;
        if item["can_explore"] != true {
            bail!(
                "{}",
                item["unavailable_reason"]
                    .as_str()
                    .unwrap_or("This source cannot refresh yet.")
            );
        }
        // Only the server-resolved definition ID crosses the execution boundary.
        // SQL, parameters and data-time scope stay bound to the saved source.
        let entry = item["id"].as_str().context("Missing query definition ID")?;
        self.preview(&source.surface_id, entry, true)
    }

    pub fn image(&self, surface: &Surface) -> Result<PathBuf> {
        validate_uuid(&surface.id)?;
        let path = string(&surface.payload, "image_url");
        if !path.starts_with("/api/calliope/") {
            bail!("This image is available in the browser.");
        }
        let r = self.request(reqwest::Method::GET, &path)?.send()?;
        if !r.status().is_success() {
            bail!("Image is unavailable ({})", r.status());
        }
        let mime = r
            .headers()
            .get(CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        let ext = match mime.split(';').next().unwrap_or("") {
            "image/png" => "png",
            "image/jpeg" => "jpg",
            "image/webp" => "webp",
            _ => bail!("Unsupported image preview"),
        };
        let mut bytes = Vec::new();
        r.take(12 * 1024 * 1024 + 1).read_to_end(&mut bytes)?;
        if bytes.len() > 12 * 1024 * 1024 {
            bail!("Image is too large for a canvas preview.");
        }
        let path = data_dir()
            .join("previews")
            .join(format!("{}.{}", surface.id, ext));
        private_write(&path, &bytes)?;
        Ok(path)
    }

    pub fn turn(
        &self,
        session: &str,
        message: &str,
        selected: Option<&str>,
        client_id: &str,
        mut receive: impl FnMut(String, Value),
    ) -> Result<()> {
        validate_uuid(session)?;
        validate_uuid(client_id)?;
        let r=self.request(reqwest::Method::POST,&format!("/api/calliope/sessions/{session}/turn"))?
            .timeout(Duration::from_secs(900))
            .json(&json!({"message":message,"selected_surface_id":selected,"client_turn_id":client_id})).send()?;
        if !r.status().is_success() {
            Self::json_response(r)?;
            bail!("Turn was rejected");
        }
        if !r
            .headers()
            .get(CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .is_some_and(|s| s.contains("text/event-stream"))
        {
            bail!("Lab did not return an event stream. Refresh before retrying.");
        }
        let mut terminal = false;
        read_sse(BufReader::new(r), &mut |event: String, value: Value| {
            terminal |= matches!(event.as_str(), "calliope.turn.completed" | "calliope.error");
            receive(event, value);
        })?;
        if !terminal {
            bail!(
                "The stream disconnected before completion. Refresh the conversation to recover its saved state"
            );
        }
        Ok(())
    }
}

fn validate_uuid(id: &str) -> Result<()> {
    uuid::Uuid::parse_str(id)?;
    Ok(())
}

pub use crate::app_stream::read_sse;

pub fn browser_link(config: &Connection, s: &Surface) -> Option<String> {
    let base = Url::parse(&config.browser_url).ok()?;
    let raw = string(&s.payload, "url");
    if !raw.is_empty() {
        let url = base.join(&raw).ok()?;
        if matches!(url.scheme(), "https" | "http") {
            return Some(url.to_string());
        }
    }
    Some(
        base.join(&format!(
            "/calliope?session={}&surface={}",
            s.session_id, s.id
        ))
        .ok()?
        .to_string(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn credentials_stay_on_configured_origin() {
        let a = Api::new(Connection::default()).unwrap();
        assert!(a.url("https://example.com/steal").is_err());
        assert!(a.url("//example.com/steal").is_err());
        assert!(a.url("/api/calliope/sessions").is_ok());
    }
    #[test]
    fn disallow_cleartext_nonlocal_credentials() {
        assert!(
            Api::new(Connection {
                base_url: "http://example.com".into(),
                ..Default::default()
            })
            .is_err()
        );
    }
    #[test]
    fn sse_multiline_and_comments() {
        let input=b": heartbeat\r\nevent: calliope.draft\r\ndata: {\"text\":\r\ndata: \"hello\"}\r\n\r\nevent: calliope.turn.completed\ndata: {}\n\n";
        let mut events = vec![];
        read_sse(&input[..], &mut |e, v| events.push((e, v))).unwrap();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].1["text"], "hello");
        assert_eq!(events[1].0, "calliope.turn.completed");
    }
}

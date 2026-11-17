//! This probe uses the existing Calliope session and HTTP contract.
//! Credentials live in the platform credential store; data stays on Calliope.
use anyhow::{Context, Result, ensure};
use reqwest::{Url, blocking::Client, redirect::Policy};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{io::Read, time::Duration};

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(default)]
pub struct Connection {
    pub base_url: String,
    pub browser_url: String,
    pub session_cookie: String,
    pub email: String,
}
impl Connection {
    pub fn load() -> Result<Self> {
        let value = crate::host::load_connection().map_err(anyhow::Error::msg)?;
        Ok(serde_json::from_str(&value)?)
    }
    pub fn save(&self) -> Result<()> {
        crate::host::save_connection(&serde_json::to_string(self)?).map_err(anyhow::Error::msg)
    }
}
#[derive(Clone)]
pub struct Api {
    pub config: Connection,
    client: Client,
}
impl Api {
    pub fn login(mut config: Connection, password: String) -> Result<Connection> {
        let api = Self::new(config.clone())?;
        let response = api
            .client
            .post(api.url("/login")?)
            .form(&[
                ("email", config.email.as_str()),
                ("password", password.as_str()),
                ("next", "/api/calliope/ui-apps"),
            ])
            .send()?;
        config.session_cookie = response
            .headers()
            .get_all(reqwest::header::SET_COOKIE)
            .iter()
            .filter_map(|v| v.to_str().ok())
            .find_map(|v| {
                v.strip_prefix("wh_session=")
                    .map(|s| s.split(';').next().unwrap_or_default().to_owned())
            })
            .context("Sign-in failed. Check your email and Calliope password.")?;
        Self::new(config.clone())?.get("/api/calliope/ui-apps")?;
        config.save()?;
        Ok(config)
    }
    pub fn new(config: Connection) -> Result<Self> {
        let url = Url::parse(&config.base_url)?;
        ensure!(
            url.scheme() == "https"
                || (url.scheme() == "http"
                    && matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"))),
            "Use HTTPS or the ADB loopback tunnel"
        );
        ensure!(
            url.username().is_empty() && url.password().is_none(),
            "Credentials cannot be embedded in URLs"
        );
        let client = Client::builder()
            .redirect(Policy::none())
            .connect_timeout(Duration::from_secs(8))
            .timeout(Duration::from_secs(45));
        #[cfg(target_os = "android")]
        let client = {
            let pem = crate::host::trusted_certificates().map_err(anyhow::Error::msg)?;
            let certificates = reqwest::Certificate::from_pem_bundle(pem.as_bytes())?;
            ensure!(!certificates.is_empty(), "Android has no trusted certificates");
            // Android roots feed rustls; iOS uses Security.framework directly.
            let mut client = client.tls_built_in_root_certs(false);
            for certificate in certificates {
                client = client.add_root_certificate(certificate);
            }
            client
        };
        Ok(Self { config, client: client.build()? })
    }
    fn url(&self, path: &str) -> Result<Url> {
        let base = Url::parse(&self.config.base_url)?;
        let url = base.join(path)?;
        ensure!(
            base.origin() == url.origin(),
            "Cross-origin credentials are forbidden"
        );
        Ok(url)
    }
    pub(crate) fn request(
        &self,
        method: reqwest::Method,
        path: &str,
    ) -> Result<reqwest::blocking::RequestBuilder> {
        Ok(self.client.request(method, self.url(path)?).header(
            reqwest::header::COOKIE,
            format!("wh_session={}", self.config.session_cookie),
        ))
    }
    pub(crate) fn json_response(response: reqwest::blocking::Response) -> Result<Value> {
        let status = response.status();
        if status.as_u16()==401 || status.is_redirection() {
            return Err(crate::app_drafts::HttpError {status:401,code:"UNAUTHORIZED".into(),message:"Your Calliope session expired. Reconnect to sign in again.".into()}.into());
        }
        ensure!(
            response
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .is_some_and(|v| v.contains("application/json")),
            "Expected a JSON response from Calliope"
        );
        let mut bytes = Vec::new();
        response.take(20_000_001).read_to_end(&mut bytes)?;
        ensure!(bytes.len() <= 20_000_000, "Response exceeds size limit");
        let value: Value = serde_json::from_slice(&bytes)?;
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
                    .and_then(Value::as_str)
                    .unwrap_or("Reconnect if your session expired")
                    .into(),
            }
            .into());
        }
        Ok(value)
    }
    pub fn get(&self, path: &str) -> Result<Value> {
        Self::json_response(self.request(reqwest::Method::GET, path)?.send()?)
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
        let text = crate::model::string(&result, "text");
        if text.trim().is_empty() {
            anyhow::bail!("No speech detected. Check your microphone and try again.");
        }
        Ok(text.trim().to_owned())
    }
    pub fn patch(&self, path: &str, body: &Value) -> Result<Value> {
        Self::json_response(
            self.request(reqwest::Method::PATCH, path)?
                .json(body)
                .send()?,
        )
    }
    pub fn post(&self, path: &str, body: &Value) -> Result<Value> {
        Self::json_response(
            self.request(reqwest::Method::POST, path)?
                .json(body)
                .send()?,
        )
    }
    pub fn asset(&self, path: &str, limit: usize) -> Result<Vec<u8>> {
        let response = self
            .request(reqwest::Method::GET, path)?
            .send()?
            .error_for_status()?;
        let mut bytes = Vec::new();
        response.take(limit as u64 + 1).read_to_end(&mut bytes)?;
        ensure!(bytes.len() <= limit, "Asset exceeds size limit");
        Ok(bytes)
    }
}

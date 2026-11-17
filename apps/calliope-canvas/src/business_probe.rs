//! Opt-in local testing socket. Never enabled in the ordinary app launch.
use serde_json::{Value, json};
use std::{
    io::{BufRead, BufReader, Write},
    sync::mpsc::{self, Receiver, Sender},
    time::Duration,
};
pub struct Request {
    pub body: Value,
    pub reply: Sender<Value>,
}
#[cfg(unix)]
pub fn start(wake: std::sync::Arc<dyn Fn() + Send + Sync>) -> Option<Receiver<Request>> {
    let path = std::path::PathBuf::from(std::env::var_os("CALLIOPE_UI_PROBE")?);
    start_at(path, wake)
}
#[cfg(unix)]
pub fn start_at(path: std::path::PathBuf, wake: std::sync::Arc<dyn Fn() + Send + Sync>) -> Option<Receiver<Request>> {
    use std::os::unix::{fs::PermissionsExt, net::UnixListener};
    let listener = UnixListener::bind(&path)
        .map_err(|e| eprintln!("UI probe: {e}"))
        .ok()?;
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).ok()?;
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { break };
            let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
            let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));
            let mut body = String::new();
            let result = (|| {
                use std::io::Read;
                BufReader::new(&mut stream)
                    .take(2_097_153)
                    .read_line(&mut body)
                    .ok()?;
                if body.len() > 2_097_152 {
                    return Some(json!({"error":"Probe request exceeds 2 MB"}));
                }
                let value: Value = serde_json::from_str(&body).ok()?;
                let (reply, response) = mpsc::channel();
                tx.send(Request { body: value, reply }).ok()?;
                wake();
                Some(
                    response
                        .recv_timeout(Duration::from_secs(5))
                        .unwrap_or(json!({"error":"UI did not respond"})),
                )
            })()
            .unwrap_or(json!({"error":"Invalid probe request"}));
            let _ = writeln!(stream, "{}", result);
        }
    });
    Some(rx)
}
#[cfg(not(unix))]
pub fn start(_: std::sync::Arc<dyn Fn() + Send + Sync>) -> Option<Receiver<Request>> {
    None
}

#[cfg(not(unix))]
pub fn start_at(_: std::path::PathBuf, _: std::sync::Arc<dyn Fn() + Send + Sync>) -> Option<Receiver<Request>> {
    None
}

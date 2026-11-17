//! Device-local evidence and immutable submissions. No stock or scheduling outbox.
use crate::model::{data_dir, private_write};
use anyhow::{Result, bail};
use serde_json::{Value, json};
fn path() -> std::path::PathBuf {
    data_dir().join("field-outbox.json")
}
pub fn load() -> Result<Vec<Value>> {
    if !path().exists() {
        return Ok(vec![]);
    }
    Ok(serde_json::from_slice(&std::fs::read(path())?)?)
}
pub fn save(items: &[Value]) -> Result<()> {
    private_write(&path(), &serde_json::to_vec(items)?)
}
fn file_path(id: &str) -> Result<std::path::PathBuf> {
    if !id.starts_with("LOCAL-") || !id.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-') {
        bail!("Invalid local evidence ID");
    }
    Ok(data_dir().join("field-files").join(format!("{id}.json")))
}
pub fn file(id: &str) -> Result<Value> {
    Ok(serde_json::from_slice(&std::fs::read(file_path(id)?)?)?)
}
pub fn capture(file: &std::path::Path) -> Result<Value> {
    use base64::Engine;
    let dir = data_dir().join("field-files");
    std::fs::create_dir_all(&dir)?;
    let used = std::fs::read_dir(&dir)?
        .filter_map(Result::ok)
        .filter_map(|e| e.metadata().ok())
        .map(|m| m.len())
        .sum::<u64>();
    if used > 40 * 1024 * 1024 {
        bail!("Device evidence storage is full. Send saved reports first.");
    }
    if std::fs::metadata(file)?.len() > 2 * 1024 * 1024 {
        bail!("Field evidence must be at most 2 MB.");
    }
    let bytes = std::fs::read(file)?;
    if bytes.is_empty() {
        bail!("Choose a non-empty file");
    }
    let mime = match file
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_lowercase()
        .as_str()
    {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "pdf" => "application/pdf",
        "txt" => "text/plain",
        _ => bail!("Choose PNG, JPEG, PDF or text"),
    };
    let id = format!("LOCAL-{}", uuid::Uuid::new_v4());
    let record = json!({"id":id,"name":file.file_name().unwrap_or_default().to_string_lossy(),"mime":mime,"data":base64::engine::general_purpose::STANDARD.encode(bytes)});
    private_write(&file_path(&id)?, &serde_json::to_vec(&record)?)?;
    Ok(record)
}
pub fn enqueue(items: &mut Vec<Value>, draft: Value) -> Result<()> {
    if items.iter().filter(|i| i["status"] != "delivered").count() >= 20 {
        bail!("Send pending reports before adding more");
    }
    let ids = draft["attachments"].as_array().cloned().unwrap_or_default();
    if ids.len() > 3 {
        bail!("Choose at most three evidence files");
    }
    let mut files = vec![];
    for id in ids {
        let f = file(id.as_str().unwrap_or(""))?;
        files.push(json!({"name":f["name"],"mime":f["mime"],"data":f["data"]}));
    }
    let id = uuid::Uuid::new_v4().to_string();
    let item = json!({"id":id,"status":"pending","error":"","body":{"requestId":id,"draft":draft,"files":files,"source":"Desktop"}});
    let mut next = items.clone();
    next.push(item);
    if serde_json::to_vec(&next)?.len() > 40 * 1024 * 1024 {
        bail!("Device report queue is full");
    }
    save(&next)?;
    *items = next;
    Ok(())
}
pub fn remove_files(item: &Value) {
    if let Some(ids) = item["body"]["draft"]["attachments"].as_array() {
        for id in ids {
            if let Some(id) = id.as_str() {
                if let Ok(path) = file_path(id) {
                    let _ = std::fs::remove_file(path);
                }
            }
        }
    }
}

pub fn remove_file(id: &str) -> Result<()> {
    let path = file_path(id)?;
    if path.exists() {
        std::fs::remove_file(path)?;
    }
    Ok(())
}

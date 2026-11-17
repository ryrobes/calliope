use std::{path::PathBuf, sync::OnceLock};
pub static FILES: OnceLock<PathBuf> = OnceLock::new();
pub fn data_dir() -> PathBuf {
    FILES
        .get()
        .expect("Mobile host supplies files directory")
        .clone()
}
pub fn string(value: &serde_json::Value, key: &str) -> String {
    value[key].as_str().unwrap_or_default().to_owned()
}
pub fn private_write(path: &std::path::Path, bytes: &[u8]) -> anyhow::Result<()> {
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(path)?;
    file.write_all(bytes)?;
    Ok(())
}

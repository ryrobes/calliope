//! Server-owned credential files take precedence over environment bootstrap values.
//! Resolve for each request so atomic file replacement needs no worker restart.
pub fn resolve(name: &str) -> Option<String> {
    resolve_with(name, |key| std::env::var(key).ok())
}

fn resolve_with(name: &str, lookup: impl Fn(&str) -> Option<String>) -> Option<String> {
    let value = if let Some(path) = lookup(&format!("{name}_FILE")) {
        // An explicitly configured, missing/unreadable file fails closed.
        let meta = std::fs::metadata(&path).ok()?;
        if !meta.is_file() || meta.len() > 65536 { return None; }
        std::fs::read_to_string(path).ok()?
    } else { lookup(name)? };
    let value = value.trim();
    (!value.is_empty() && !value.contains(['\n', '\r'])).then(|| value.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rotation_and_missing_file_never_fall_back_to_stale_env() {
        let path = std::env::temp_dir().join(format!("clover-key-test-{}", std::process::id()));
        let lookup = |key: &str| Some(if key.ends_with("_FILE") { path.to_string_lossy().to_string() } else { "stale".into() });
        std::fs::write(&path, "first\n").unwrap();
        assert_eq!(resolve_with("TEST_KEY", lookup).as_deref(), Some("first"));
        std::fs::write(&path, "second").unwrap();
        assert_eq!(resolve_with("TEST_KEY", lookup).as_deref(), Some("second"));
        std::fs::remove_file(&path).unwrap();
        assert_eq!(resolve_with("TEST_KEY", lookup), None);
        assert_eq!(resolve_with("TEST_KEY", |k| (k == "TEST_KEY").then(|| "env-key".into())).as_deref(), Some("env-key"));
    }
}

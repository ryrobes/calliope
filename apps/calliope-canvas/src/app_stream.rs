//! Shared bounded Calliope SSE reader.
use anyhow::{Context, Result, bail};
use serde_json::Value;
use std::io::BufRead;

pub fn read_sse(reader: impl BufRead, receive: &mut impl FnMut(String, Value)) -> Result<()> {
    let mut event = String::new();
    let mut data = String::new();
    for line in reader.lines() {
        let line = line?;
        if line.is_empty() {
            if !data.is_empty() {
                let value =
                    serde_json::from_str(data.trim_end()).context("Invalid event from Lab")?;
                receive(event.clone(), value);
            }
            event.clear();
            data.clear();
        } else if let Some(s) = line.strip_prefix("event:") {
            event = s.trim().into();
        } else if let Some(s) = line.strip_prefix("data:") {
            data.push_str(s.strip_prefix(' ').unwrap_or(s));
            data.push('\n');
        }
        if data.len() > 8 * 1024 * 1024 {
            bail!("Lab event exceeded the client size limit.");
        }
    }
    if !data.is_empty() {
        receive(event, serde_json::from_str(data.trim_end())?);
    }
    Ok(())
}

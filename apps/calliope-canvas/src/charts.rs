//! Browser-free Vega-Lite rasterization and picking through pinned vl-convert.
//! A Python ABI helper exports the image and scene; GPUI handles pointer events.
use a2ui_gpui::vega::{ChartOutput, ChartScene};
use serde_json::Value;
use std::{
    fs,
    process::{Command, Stdio},
    time::{Duration, Instant},
};

pub fn validate(spec: &Value) -> Result<(), String> {
    fn visit(v: &Value, depth: usize) -> Result<(), String> {
        if depth > 24 {
            return Err("Chart nesting is too deep".into());
        }
        match v {
            Value::Array(a) => {
                for v in a {
                    visit(v, depth + 1)?;
                }
            }
            Value::Object(o) => {
                for (k, v) in o {
                    if matches!(k.as_str(), "url" | "href" | "signals")
                        || (k == "mark" && (v == "image" || v["type"] == "image"))
                        || (k == "type" && v == "image")
                    {
                        return Err(format!("Chart capability is not enabled: {k}"));
                    }
                    if k != "values" && k != "datasets" {
                        visit(v, depth + 1)?;
                    }
                }
            }
            _ => {}
        }
        Ok(())
    }
    if !spec.is_object() || spec.to_string().len() > 1_000_000 {
        return Err("Chart requires a bounded Vega-Lite object".into());
    }
    visit(spec, 0)
}

pub fn render(spec: Value, width: u32, height: u32) -> Result<ChartOutput, String> {
    validate(&spec)?;
    let render = || -> anyhow::Result<ChartOutput> {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        let helper = root.join("business/render_chart.py");
        anyhow::ensure!(
            root.join("local/vega/python/vl_convert").is_dir(),
            "Install chart renderer: python3 business/install-vega.py"
        );
        let work = tempfile::tempdir()?;
        let input = work.path().join("chart.json");
        let output = work.path().join("chart.png");
        let stderr = work.path().join("stderr.log");
        fs::write(&input, serde_json::to_vec(&spec)?)?;
        let mut command = Command::new("python3");
        command
            .arg("-I")
            .arg(helper)
            .arg(input)
            .arg(&output)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(fs::File::create(&stderr)?);
        let fonts = std::env::var_os("CALLIOPE_CHART_FONT_DIR")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| crate::model::data_dir().join("fonts"));
        command.arg(if fonts.is_dir() {
            fonts.as_os_str()
        } else {
            std::ffi::OsStr::new("")
        });
        command.arg(width.to_string()).arg(height.to_string());
        let mut child = command.spawn()?;
        let started = Instant::now();
        let status = loop {
            if let Some(status) = child.try_wait()? {
                break status;
            }
            if started.elapsed() > Duration::from_secs(8)
                || fs::metadata(&stderr)?.len() > 1_000_000
            {
                let _ = child.kill();
                let _ = child.wait();
                anyhow::bail!("Chart rendering exceeded its time/output budget");
            }
            std::thread::sleep(Duration::from_millis(20));
        };
        anyhow::ensure!(
            status.success(),
            "Vega rendering failed: {}",
            fs::read_to_string(stderr)?
                .chars()
                .take(500)
                .collect::<String>()
        );
        anyhow::ensure!(
            fs::metadata(&output)?.len() <= 8_000_000,
            "Chart image exceeds its output budget"
        );
        let metadata = work.path().join("chart.png.json");
        anyhow::ensure!(
            fs::metadata(&metadata)?.len() <= 8_000_000,
            "Chart interactions exceed their output budget"
        );
        let scene: ChartScene = serde_json::from_slice(&fs::read(metadata)?)?;
        anyhow::ensure!(scene.valid(), "Invalid chart geometry");
        Ok(ChartOutput {
            png: fs::read(output)?,
            scene,
        })
    };
    render().map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn rejects_remote_data_but_accepts_vega_expressions() {
        let mut spec = json!({"data":{"values":[]},"mark":"bar"});
        assert!(validate(&spec).is_ok());
        spec["data"]["url"] = json!("file:///etc/passwd");
        assert!(validate(&spec).is_err());
        spec["data"].as_object_mut().unwrap().remove("url");
        spec["transform"] = json!([{"calculate":"1+1","as":"x"}]);
        assert!(validate(&spec).is_ok());
    }
    #[test]
    #[ignore = "requires business/install-vega.py"]
    fn renders_two_actual_pixel_sizes() {
        let spec = json!({"data":{"values":[{"x":"Repair","y":4},{"x":"Install","y":2}]},"mark":"bar","encoding":{"x":{"field":"x","type":"nominal"},"y":{"field":"y","type":"quantitative"}}});
        for width in [280, 600] {
            let output = render(spec.clone(), width, 260).unwrap();
            assert_eq!(output.scene.hits.len(), 2);
            let png = output.png;
            assert_eq!(&png[..8], b"\x89PNG\r\n\x1a\n");
            assert_eq!(
                u32::from_be_bytes(png[16..20].try_into().unwrap()),
                width * 2
            );
            assert_eq!(u32::from_be_bytes(png[20..24].try_into().unwrap()), 520);
        }
    }
}

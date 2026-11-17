//! Published apps render through Calliope; identical transport on desktop/mobile.
use a2ui_gpui::vega::{ChartOutput, ChartRenderer, ChartScene};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::json;

pub fn renderer(api: crate::api::Api) -> ChartRenderer {
    std::sync::Arc::new(move |spec, width, height| {
        let render = || -> anyhow::Result<ChartOutput> {
            let value = api.post("/api/calliope/ui-charts/render", &json!({
                "spec":spec, "width":width, "height":height
            }))?;
            let encoded = value["png"].as_str().ok_or_else(|| anyhow::anyhow!("Missing chart image"))?;
            anyhow::ensure!(encoded.len() <= 10_666_668, "Chart image exceeds size limit");
            let png = STANDARD.decode(encoded)?;
            anyhow::ensure!(png.starts_with(b"\x89PNG\r\n\x1a\n") && png.len() >= 24, "Invalid chart PNG");
            let w = u32::from_be_bytes(png[16..20].try_into()?);
            let h = u32::from_be_bytes(png[20..24].try_into()?);
            anyhow::ensure!(w > 0 && h > 0 && w <= 8192 && h <= 8192 && u64::from(w)*u64::from(h) <= 32_000_000, "Chart image dimensions exceed budget");
            let scene: ChartScene = serde_json::from_value(value["scene"].clone())?;
            anyhow::ensure!(scene.valid(), "Invalid chart geometry");
            Ok(ChartOutput { png, scene })
        };
        render().map_err(|e| e.to_string())
    })
}

/// The host's theme rides along as Vega-Lite `config`; a spec's own `config`
/// overrides it key by key. Works for server and local renderers alike.
pub fn themed(base: ChartRenderer, theme: serde_json::Value) -> ChartRenderer {
    use serde_json::Value;
    fn merge(into: &mut Value, over: Value) {
        match (into, over) {
            (Value::Object(into), Value::Object(over)) => {
                for (key, value) in over {
                    merge(into.entry(key).or_insert(Value::Null), value);
                }
            }
            // A spec without its own config keeps the theme whole.
            (_, Value::Null) => {}
            (into, over) => *into = over,
        }
    }
    std::sync::Arc::new(move |mut spec: Value, width, height| {
        let mut config = theme.clone();
        merge(&mut config, spec["config"].take());
        spec["config"] = config;
        base(spec, width, height)
    })
}

#[cfg(test)]
mod tests {
    use serde_json::{Value, json};
    #[test]
    fn the_spec_config_wins_and_a_missing_one_keeps_the_theme() {
        let seen = std::sync::Arc::new(std::sync::Mutex::new(Vec::<Value>::new()));
        let sink = seen.clone();
        let render = super::themed(
            std::sync::Arc::new(move |spec, _, _| {
                sink.lock().unwrap().push(spec);
                Err("captured".into())
            }),
            json!({"background": "transparent", "axis": {"labelColor": "grey", "gridColor": "dim"}}),
        );
        let _ = render(json!({"mark": "bar"}), 300, 200);
        let _ = render(json!({"mark": "bar", "config": {"axis": {"labelColor": "red"}}}), 300, 200);
        let seen = seen.lock().unwrap();
        assert_eq!(seen[0]["config"]["axis"]["labelColor"], "grey");
        assert_eq!(seen[1]["config"]["axis"], json!({"labelColor": "red", "gridColor": "dim"}));
        assert_eq!(seen[1]["config"]["background"], "transparent");
    }
}

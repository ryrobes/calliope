//! Bridge the Warehouse appearance contract into native semantic primitives.
//! Palette branches mirror warehouse-theme.src.js; components never parse CSS.
use crate::{
    api::Api,
    model::{data_dir, private_write, string},
};
use anyhow::Result;
use gpui::{App, Hsla, Rgba, SharedString, px, rgb};
use gpui_component::{Theme, ThemeColor, ThemeMode};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::HashMap,
    hash::{Hash, Hasher},
    path::PathBuf,
};

#[derive(Clone, Copy)]
pub struct Typography {
    pub caption: f32,
    pub body: f32,
    pub heading: f32,
    pub title: f32,
    pub display: f32,
}

#[derive(Clone)]
pub struct CanvasTheme {
    pub dark: bool,
    pub name: String,
    pub source: String,
    pub bg: Rgba,
    pub panel: Rgba,
    pub card: Rgba,
    pub hover: Rgba,
    pub line: Rgba,
    pub text: Rgba,
    pub muted: Rgba,
    pub accent: Rgba,
    pub on_accent: Rgba,
    pub accent_soft: Rgba,
    pub success: Rgba,
    pub warning: Rgba,
    pub danger: Rgba,
    pub info: Rgba,
    pub grid: Rgba,
    pub scrim: Rgba,
    pub charts: [Rgba; 6],
    pub tokens: HashMap<String, Rgba>,
    pub wallpaper: Option<PathBuf>,
    pub font_body: SharedString,
    pub font_mono: SharedString,
    pub font_display: SharedString,
    pub typography: Typography,
    pub radius: f32,
}

#[derive(Serialize, Deserialize)]
#[serde(default)]
pub struct Preferences {
    pub wallpaper_strength: f32,
    // None inherits the web preference. A toggle only mutes this device.
    pub read_aloud: Option<bool>,
}
impl Default for Preferences {
    fn default() -> Self {
        Self {
            wallpaper_strength: 0.22,
            read_aloud: None,
        }
    }
}
impl Preferences {
    pub fn load() -> Self {
        let mut value: Self = std::fs::read(data_dir().join("preferences.json"))
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or_default();
        value.wallpaper_strength = value.wallpaper_strength.clamp(0., 0.6);
        value
    }
    pub fn save(&self) -> Result<()> {
        private_write(
            &data_dir().join("preferences.json"),
            &serde_json::to_vec_pretty(self)?,
        )
    }
}

pub struct Assets {
    pub theme: CanvasTheme,
    pub fonts: Vec<Vec<u8>>,
    pub warnings: Vec<String>,
}

const FONT_NAMES: [&str; 6] = [
    "LTSuperior-Regular.otf",
    "LTSuperior-Medium.otf",
    "LTSuperior-SemiBold.otf",
    "LTSuperior-Bold.otf",
    "LTSuperiorMono-Regular.otf",
    "LTSuperiorSerif-Regular.otf",
];

fn font_cache(base_url: &str) -> PathBuf {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    base_url.hash(&mut hasher);
    data_dir()
        .join("fonts")
        .join(format!("{:x}", hasher.finish()))
}

/// Local workspaces retain typography even when the Lab session has expired.
pub fn cached_fonts(base_url: &str) -> Vec<Vec<u8>> {
    let dir = font_cache(base_url);
    FONT_NAMES
        .iter()
        .filter_map(|name| std::fs::read(dir.join(name)).ok())
        .collect()
}

pub fn load(api: &Api) -> Result<Assets> {
    let appearance = api.get("/api/calliope/appearance")?;
    let mut theme = CanvasTheme::from_appearance(&appearance);
    let mut warnings = Vec::new();
    if let Some(path) = wallpaper_url(&appearance) {
        match api.asset(&path, 20 * 1024 * 1024) {
            Ok(bytes) => {
                // Content identity reuses the GPU texture when the wallpaper is unchanged.
                let mut hash = std::collections::hash_map::DefaultHasher::new();
                bytes.hash(&mut hash);
                let extension = if bytes.starts_with(b"\x89PNG") {
                    "png"
                } else if bytes.starts_with(b"\xff\xd8") {
                    "jpg"
                } else {
                    "webp"
                };
                let file = data_dir()
                    .join("appearance")
                    .join(format!("{:x}.{extension}", hash.finish()));
                match private_write(&file, &bytes) {
                    Ok(()) => theme.wallpaper = Some(file),
                    Err(e) => warnings.push(e.to_string()),
                }
            }
            Err(e) => warnings.push(format!("Wallpaper: {e}")),
        }
    }
    let mut fonts = Vec::new();
    let dir = font_cache(&api.config.base_url);
    for name in FONT_NAMES {
        let path = dir.join(name);
        let result = std::fs::read(&path)
            .map_err(anyhow::Error::from)
            .or_else(|_| {
                let bytes = api.asset(&format!("/theme/fonts/{name}"), 2 * 1024 * 1024)?;
                private_write(&path, &bytes)?;
                Ok::<_, anyhow::Error>(bytes)
            });
        match result {
            Ok(bytes) => fonts.push(bytes),
            Err(e) => warnings.push(format!("Font {name}: {e}")),
        }
    }
    Ok(Assets {
        theme,
        fonts,
        warnings,
    })
}

pub fn wallpaper_url(remote: &Value) -> Option<String> {
    let appearance = &remote["appearance"];
    if appearance["background"]["mode"].as_str().unwrap_or("image") != "image" {
        return None;
    }
    let source = &appearance["source"];
    match source["kind"].as_str() {
        Some("upload") if remote["has_upload"] == true => {
            Some("/api/calliope/appearance/image".into())
        }
        Some("library") => {
            let url = string(source, "url");
            if url.starts_with("/theme/images/full/") || url.starts_with("/api/desktop/wallpapers/")
            {
                Some(url)
            } else {
                None
            }
        }
        _ => None,
    }
}

impl Default for CanvasTheme {
    fn default() -> Self {
        Self::from_appearance(&Value::Null)
    }
}

fn css(c: Rgba) -> String {
    let byte = |v: f32| (v.clamp(0., 1.) * 255.).round() as u8;
    format!("rgba({},{},{},{:.3})", byte(c.r), byte(c.g), byte(c.b), c.a.clamp(0., 1.))
}

impl CanvasTheme {
    /// Vega-Lite `config` matching the web Stage's `vegaThemeConfig`, for charts
    /// rendered natively; a spec's own `config` still wins where it is set.
    pub fn vega_config(&self) -> Value {
        let (ink, dim, line, grid, font) = (css(self.text), css(self.muted), css(self.line), css(self.grid), self.font_body.to_string());
        let palette: Vec<String> = self.charts.iter().map(|c| css(*c)).collect();
        serde_json::json!({
            "background": "transparent",
            "font": font,
            "view": {"stroke": null},
            "axis": {"labelColor": dim, "titleColor": ink, "gridColor": grid, "domainColor": line, "tickColor": line,
                     "labelFont": font, "titleFont": font, "labelFontSize": 11, "titleFontSize": 11, "titleFontWeight": 600},
            "legend": {"labelColor": dim, "titleColor": ink, "labelFont": font, "titleFont": font},
            "title": {"color": ink, "font": font, "anchor": "start", "fontWeight": 600, "fontSize": 14, "subtitleColor": dim, "subtitleFont": font},
            "header": {"labelColor": dim, "titleColor": ink, "labelFont": font, "titleFont": font},
            "range": {"category": palette, "ordinal": {"scheme": "blues"}},
            "mark": {"color": palette[0]},
            "bar": {"cornerRadiusEnd": 2},
            "line": {"strokeWidth": 2},
            "point": {"filled": true, "size": 48},
            "area": {"opacity": 0.55},
        })
    }

    pub fn from_appearance(remote: &Value) -> Self {
        let dark = remote["mode"] != "light";
        let appearance = &remote["appearance"];
        let palette = &appearance["palette"];
        let hue = palette["baseHue"].as_f64().unwrap_or(250.) as f32;
        let parse = |key: &str, fallback: Rgba| {
            palette[key]
                .as_str()
                .and_then(parse_color)
                .unwrap_or(fallback)
        };
        let accent = parse(
            if dark { "vibrant" } else { "darkVibrant" },
            rgb(if dark { 0x92bcae } else { 0x27614e }),
        );
        let bg = if dark {
            oklch(0.13, 0.035, hue, 1.)
        } else {
            oklch(0.96, 0.008, 95., 1.)
        };
        let panel = if dark {
            oklch(0.17, 0.035, hue, 1.)
        } else {
            oklch(0.99, 0.004, 95., 1.)
        };
        let hover = if dark {
            oklch(0.20, 0.04, hue, 1.)
        } else {
            oklch(0.94, 0.012, hue, 1.)
        };
        let text = if dark {
            oklch(0.92, 0., 0., 1.)
        } else {
            oklch(0.21, 0.015, 260., 1.)
        };
        let mut tokens: HashMap<String, Rgba> = appearance["tokens"]
            .as_object()
            .map(|v| {
                v.iter()
                    .filter_map(|(k, v)| Some((k.clone(), parse_color(v.as_str()?)?)))
                    .collect()
            })
            .unwrap_or_default();
        // The web client re-derives these structural tokens for the selected mode,
        // rather than trusting a saved token snapshot from the opposite mode.
        for (key, color) in [
            ("--background", bg),
            ("--void", bg),
            ("--block-bg", panel),
            ("--panel", panel),
            ("--panel-raised", hover),
            ("--block-bg-hover", hover),
            ("--foreground", text),
            ("--bone", text),
            ("--main", accent),
            ("--ring", accent),
        ] {
            tokens.insert(key.into(), color);
        }
        let chroma = (palette["chroma"].as_f64().unwrap_or(0.025) as f32
            * if dark { 6. } else { 7. })
        .clamp(
            if dark { 0.12 } else { 0.13 },
            if dark { 0.20 } else { 0.18 },
        );
        let charts = if dark {
            [
                accent,
                oklch(0.72, chroma * 0.9, hue + 90., 1.),
                oklch(0.80, chroma * 0.8, hue + 180., 1.),
                oklch(0.70, chroma, hue + 240., 1.),
                oklch(0.72, chroma * 0.85, hue + 320., 1.),
                oklch(0.74, chroma * 0.95, hue + 290., 1.),
            ]
        } else {
            [
                accent,
                oklch(0.58, chroma * 0.92, hue + 90., 1.),
                oklch(0.66, chroma * 0.82, hue + 180., 1.),
                oklch(0.60, chroma, hue + 240., 1.),
                oklch(0.60, chroma * 0.92, hue + 320., 1.),
                oklch(0.64, chroma * 0.96, hue + 290., 1.),
            ]
        };
        for (i, color) in charts.iter().enumerate() {
            tokens.insert(format!("--chart-{}", i + 1), *color);
        }
        Self {
            dark,
            name: appearance["source"]["name"]
                .as_str()
                .unwrap_or("Calliope")
                .to_owned(),
            source: remote["source"].as_str().unwrap_or("builtin").to_owned(),
            bg,
            panel,
            card: hover,
            hover: mix(hover, accent, 0.07),
            line: mix(panel, text, if dark { 0.16 } else { 0.20 }),
            text,
            muted: mix(panel, text, if dark { 0.66 } else { 0.70 }),
            accent,
            on_accent: if dark { rgb(0x000000) } else { rgb(0xffffff) },
            accent_soft: mix(panel, accent, 0.15),
            success: oklch(if dark { 0.72 } else { 0.58 }, 0.17, 145., 1.),
            warning: oklch(if dark { 0.80 } else { 0.71 }, 0.16, 85., 1.),
            danger: oklch(if dark { 0.65 } else { 0.58 }, 0.21, 25., 1.),
            info: oklch(if dark { 0.70 } else { 0.58 }, 0.14, 240., 1.),
            grid: with_alpha(text, if dark { 0.11 } else { 0.10 }),
            scrim: with_alpha(rgb(0x000000), 0.62),
            charts,
            tokens,
            wallpaper: None,
            font_body: "LT Superior".into(),
            font_mono: "LT Superior Mono".into(),
            font_display: "LT Superior Serif".into(),
            typography: Typography {
                caption: 11.,
                body: 14.,
                heading: 18.,
                title: 24.,
                display: 38.,
            },
            radius: 13.,
        }
    }

    /// Apply every gpui-component color role, so inputs, Markdown, menus and
    /// future controls inherit the same palette instead of the toolkit default.
    pub fn apply(&self, cx: &mut App) {
        Theme::change(
            if self.dark {
                ThemeMode::Dark
            } else {
                ThemeMode::Light
            },
            None,
            cx,
        );
        let mut colors = serde_json::to_value(Theme::global(cx).colors).unwrap();
        for (key, value) in colors.as_object_mut().unwrap() {
            let role = key.strip_prefix("button_").unwrap_or(key);
            let color = if key.contains("foreground") || key.ends_with("_text") {
                if role.starts_with("primary") {
                    self.on_accent
                } else if role.starts_with("muted") {
                    self.muted
                } else {
                    self.text
                }
            } else if key.contains("border") || key == "input" {
                self.line
            } else if role.starts_with("danger") || key == "bearish" {
                self.danger
            } else if role.starts_with("warning") {
                self.warning
            } else if role.starts_with("success") || key == "bullish" {
                self.success
            } else if role.starts_with("info") {
                self.info
            } else if role.starts_with("primary")
                || matches!(
                    key.as_str(),
                    "caret"
                        | "ring"
                        | "link"
                        | "link_active"
                        | "link_hover"
                        | "slider_bar"
                        | "progress_bar"
                )
            {
                self.accent
            } else if key == "selection" || key.contains("active") || key == "drop_target" {
                self.accent_soft
            } else if key.contains("hover") {
                self.hover
            } else if let Some(index) = key.strip_prefix("chart_") {
                self.charts[index.parse::<usize>().unwrap_or(1).saturating_sub(1).min(5)]
            } else if key == "background" {
                self.bg
            } else if key.contains("scrollbar_thumb") {
                self.muted
            } else {
                self.panel
            };
            *value = serde_json::to_value(Hsla::from(color)).unwrap();
        }
        let theme = Theme::global_mut(cx);
        theme.colors = serde_json::from_value::<ThemeColor>(colors).unwrap();
        theme.font_family = self.font_body.clone();
        theme.mono_font_family = self.font_mono.clone();
        theme.font_size = px(self.typography.body);
        theme.mono_font_size = px(self.typography.caption);
        theme.radius = px(8.);
        theme.radius_lg = px(self.radius);
        theme.colors.link = self
            .tokens
            .get("--main")
            .copied()
            .unwrap_or(self.accent)
            .into();
    }
}

pub fn with_alpha(mut color: Rgba, alpha: f32) -> Rgba {
    color.a = alpha.clamp(0., 1.);
    color
}
pub fn mix(a: Rgba, b: Rgba, t: f32) -> Rgba {
    Rgba {
        r: a.r + (b.r - a.r) * t,
        g: a.g + (b.g - a.g) * t,
        b: a.b + (b.b - a.b) * t,
        a: a.a + (b.a - a.a) * t,
    }
}

/// CSS OKLCH to GPUI's sRGB, using the same Oklab matrices as GPUI's renderer.
/// Out-of-gamut channels are clipped; alpha remains independent of lightness.
pub fn oklch(l: f32, c: f32, h: f32, alpha: f32) -> Rgba {
    let a = c * h.to_radians().cos();
    let b = c * h.to_radians().sin();
    let ll = (l + 0.39633778 * a + 0.21580376 * b).powi(3);
    let mm = (l - 0.105561346 * a - 0.06385417 * b).powi(3);
    let ss = (l - 0.08948418 * a - 1.2914855 * b).powi(3);
    let channel = |v: f32| {
        (if v <= 0.0031308 {
            12.92 * v
        } else {
            1.055 * v.powf(1. / 2.4) - 0.055
        })
        .clamp(0., 1.)
    };
    Rgba {
        r: channel(4.0767417 * ll - 3.3077116 * mm + 0.23096994 * ss),
        g: channel(-1.268438 * ll + 2.6097574 * mm - 0.3413194 * ss),
        b: channel(-0.0041960863 * ll - 0.7034186 * mm + 1.7076147 * ss),
        a: alpha.clamp(0., 1.),
    }
}

fn parse_color(raw: &str) -> Option<Rgba> {
    let raw = raw.trim();
    if let Some(hex) = raw.strip_prefix('#') {
        return if hex.len() == 6 {
            u32::from_str_radix(hex, 16).ok().map(rgb)
        } else {
            None
        };
    }
    let inner = raw
        .strip_prefix("oklch(")?
        .strip_suffix(')')?
        .replace('/', " ");
    let parts: Vec<_> = inner.split_whitespace().collect();
    if parts.len() < 3 || parts.len() > 4 {
        return None;
    }
    let number = |s: &str| -> Option<f32> {
        let v: f32 = s.trim_end_matches('%').parse().ok()?;
        if v.is_finite() {
            Some(if s.ends_with('%') { v / 100. } else { v })
        } else {
            None
        }
    };
    Some(oklch(
        number(parts[0])?,
        number(parts[1])?,
        number(parts[2])?,
        if parts.len() == 4 {
            number(parts[3])?
        } else {
            1.
        },
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn chart_config_uses_the_theme_palette() {
        let theme = CanvasTheme::from_appearance(&json!({"mode":"dark","appearance":{"palette":{}}}));
        let config = theme.vega_config();
        assert_eq!(config["range"]["category"].as_array().unwrap().len(), 6);
        assert_eq!(config["mark"]["color"], config["range"]["category"][0]);
        assert!(config["axis"]["labelColor"].as_str().unwrap().starts_with("rgba("));
        assert_eq!(config["background"], "transparent");
    }
    #[test]
    fn oklch_black_white_alpha_and_invalid_values() {
        let white = parse_color("oklch(100% 0 0 / 0.4)").unwrap();
        assert!((white.r - 1.).abs() < 0.001 && (white.a - 0.4).abs() < 0.001);
        let black = parse_color("oklch(0% 0 0)").unwrap();
        assert_eq!(black.r, 0.);
        assert!(parse_color("oklch(NaN 0 0)").is_none());
        assert!(parse_color("url(https://invalid)").is_none());
    }
    #[test]
    fn selected_mode_wins_over_old_saved_tokens() {
        let theme = CanvasTheme::from_appearance(&json!({"mode":"light", "appearance": {
            "tokens":{"--background":"#000000"}, "palette":{"baseHue":291,"darkVibrant":"#224466"}}}));
        assert!(theme.bg.r > 0.8);
        assert!((theme.accent.b - 0.4).abs() < 0.001);
    }
    #[test]
    fn only_supported_same_origin_wallpapers_are_loaded() {
        assert!(
            wallpaper_url(
                &json!({"appearance":{"source":{"kind":"library","url":"https://elsewhere/image"}}})
            )
            .is_none()
        );
        assert!(wallpaper_url(&json!({"has_upload":true,"appearance":{"source":{"kind":"upload"},"background":{"mode":"solid"}}})).is_none());
        assert_eq!(
            wallpaper_url(&json!({"has_upload":true,"appearance":{"source":{"kind":"upload"}}})),
            Some("/api/calliope/appearance/image".into())
        );
    }
}

use crate::{config::Config, fetch};
use anydoc::Format;
use firecrawl_html_extractor::{extract, ExtractOptions};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::time::Instant;
use url::Url;

#[derive(Clone, Debug, Deserialize)]
pub struct ScrapeInput {
    pub url: String,
    #[serde(default)]
    pub favor_precision: bool,
    #[serde(default)]
    pub favor_recall: bool,
    #[serde(default)]
    pub include_links: Option<bool>,
    #[serde(default)]
    pub include_tables: Option<bool>,
    #[serde(default)]
    pub include_images: bool,
    #[serde(default)]
    pub output_text: bool,
    #[serde(default)]
    pub target_language: Option<String>,
    #[serde(default)]
    pub max_bytes: Option<usize>,
    #[serde(default)]
    pub timeout_ms: Option<u64>,
    /// Optional HTTP validators used by durable research/watch clients.
    /// Clover Web still performs the request and all SSRF checks; a matching
    /// validator returns an explicit 304-shaped success without extraction.
    #[serde(default)]
    pub if_none_match: Option<String>,
    #[serde(default)]
    pub if_modified_since: Option<String>,
}

impl ScrapeInput {
    pub fn from_value(value: Value) -> Result<Self, ServiceError> {
        if let Value::String(url) = value {
            return Ok(Self {
                url,
                favor_precision: false,
                favor_recall: false,
                include_links: None,
                include_tables: None,
                include_images: false,
                output_text: false,
                target_language: None,
                max_bytes: None,
                timeout_ms: None,
                if_none_match: None,
                if_modified_since: None,
            });
        }
        serde_json::from_value(value).map_err(|error| {
            ServiceError::new(
                "invalid_input",
                format!("input must be a URL string or scrape options object: {error}"),
            )
        })
    }
}

#[derive(Debug, Serialize)]
pub struct ScrapeSuccess {
    pub ok: bool,
    pub not_modified: bool,
    pub url: String,
    pub final_url: String,
    pub status_code: u16,
    pub content_type: Option<String>,
    pub format: String,
    pub source: &'static str,
    pub markdown: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<PageMetadata>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub page_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub extraction_quality: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sha256: Option<String>,
    pub bytes: usize,
    pub redirects: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub etag: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_modified: Option<String>,
    pub fetch_ms: f64,
    pub extract_ms: f64,
}

#[derive(Debug, Serialize)]
pub struct PageMetadata {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub author: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub published_date: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub site_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub image_url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub canonical_url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub language: Option<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub keywords: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub schema_type: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct ServiceError {
    pub code: &'static str,
    pub message: String,
}

impl ServiceError {
    pub fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

pub async fn scrape(input: ScrapeInput, config: &Config) -> Result<ScrapeSuccess, ServiceError> {
    if input.favor_precision && input.favor_recall {
        return Err(ServiceError::new(
            "invalid_options",
            "favor_precision and favor_recall cannot both be true",
        ));
    }
    let fetched = fetch::fetch(
        &input.url,
        config,
        input.max_bytes,
        input.timeout_ms,
        input.if_none_match.as_deref(),
        input.if_modified_since.as_deref(),
    )
    .await
    .map_err(|error| ServiceError::new(error.code, error.message))?;
    if fetched.not_modified {
        return Ok(ScrapeSuccess {
            ok: true,
            not_modified: true,
            url: input.url,
            final_url: fetched.final_url.to_string(),
            status_code: fetched.status_code,
            content_type: fetched.content_type,
            format: "not_modified".into(),
            source: "http-cache",
            markdown: String::new(),
            text: None,
            metadata: None,
            page_type: None,
            extraction_quality: None,
            sha256: None,
            bytes: 0,
            redirects: fetched.redirects,
            etag: fetched.etag,
            last_modified: fetched.last_modified,
            fetch_ms: fetched.duration_ms,
            extract_ms: 0.0,
        });
    }
    let sha256 = hex::encode(Sha256::digest(&fetched.body));
    let bytes = fetched.body.len();
    let extract_started = Instant::now();
    let content_type = normalized_content_type(fetched.content_type.as_deref());

    let extracted = if is_html(&content_type, &fetched.body) {
        extract_html(&fetched.final_url, &fetched.body, &input)?
    } else if is_text(&content_type) {
        Extracted {
            format: text_format(&content_type).to_string(),
            source: "text",
            markdown: render_text(&content_type, &fetched.body),
            text: None,
            metadata: None,
            page_type: None,
            extraction_quality: None,
        }
    } else {
        extract_document(&fetched.final_url, &content_type, &fetched.body)?
    };

    Ok(ScrapeSuccess {
        ok: true,
        not_modified: false,
        url: input.url,
        final_url: fetched.final_url.to_string(),
        status_code: fetched.status_code,
        content_type: fetched.content_type,
        format: extracted.format,
        source: extracted.source,
        markdown: extracted.markdown,
        text: extracted.text,
        metadata: extracted.metadata,
        page_type: extracted.page_type,
        extraction_quality: extracted.extraction_quality,
        sha256: Some(sha256),
        bytes,
        redirects: fetched.redirects,
        etag: fetched.etag,
        last_modified: fetched.last_modified,
        fetch_ms: fetched.duration_ms,
        extract_ms: extract_started.elapsed().as_secs_f64() * 1000.0,
    })
}

struct Extracted {
    format: String,
    source: &'static str,
    markdown: String,
    text: Option<String>,
    metadata: Option<PageMetadata>,
    page_type: Option<String>,
    extraction_quality: Option<f32>,
}

fn extract_html(url: &Url, body: &[u8], input: &ScrapeInput) -> Result<Extracted, ServiceError> {
    let html = String::from_utf8_lossy(body);
    let options = ExtractOptions {
        url: Some(url.to_string()),
        favor_precision: input.favor_precision,
        favor_recall: input.favor_recall,
        output_text: input.output_text,
        output_decisions: false,
        target_language: input.target_language.clone(),
        page_type_override: None,
        include_links: input.include_links.unwrap_or(true),
        include_tables: input.include_tables.unwrap_or(true),
        include_images: input.include_images,
        include_metadata: true,
        min_extraction_length: 25,
    };
    let result = extract(&html, &options)
        .map_err(|error| ServiceError::new("extract_failed", error.to_string()))?;
    if let Some(error) = result.error_reason {
        return Err(ServiceError::new("extract_failed", error.to_string()));
    }
    let metadata = result.metadata.map(|metadata| PageMetadata {
        title: metadata.title,
        description: metadata.description,
        author: metadata.author,
        published_date: metadata.published_date,
        site_name: metadata.site_name,
        image_url: metadata.image_url,
        canonical_url: metadata.canonical_url,
        language: metadata.language,
        keywords: metadata.keywords,
        schema_type: metadata.schema_type,
    });
    Ok(Extracted {
        format: "html".into(),
        source: "firecrawl-html-extractor",
        markdown: result.markdown,
        text: result.text,
        metadata,
        page_type: Some(result.page_type.to_string()),
        extraction_quality: Some(result.extraction_quality),
    })
}

fn extract_document(url: &Url, content_type: &str, body: &[u8]) -> Result<Extracted, ServiceError> {
    let format = Format::from_bytes(body)
        .or_else(|| format_from_content_type(content_type))
        .or_else(|| {
            url.path_segments()
                .and_then(|mut segments| segments.next_back())
                .and_then(|name| name.rsplit_once('.').map(|(_, extension)| extension))
                .and_then(Format::from_extension)
        })
        .ok_or_else(|| {
            ServiceError::new(
                "unsupported_content",
                format!(
                    "content type '{}' is not HTML, text, JSON, or a supported document",
                    display_content_type(content_type)
                ),
            )
        })?;
    let markdown = anydoc::to_markdown_bytes(body, format)
        .map_err(|error| ServiceError::new("document_parse_failed", error.to_string()))?;
    Ok(Extracted {
        format: format_name(format).into(),
        source: "anydoc",
        markdown,
        text: None,
        metadata: None,
        page_type: None,
        extraction_quality: None,
    })
}

fn normalized_content_type(content_type: Option<&str>) -> String {
    content_type
        .unwrap_or("")
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase()
}

fn display_content_type(content_type: &str) -> &str {
    if content_type.is_empty() {
        "unknown"
    } else {
        content_type
    }
}

fn is_html(content_type: &str, body: &[u8]) -> bool {
    if matches!(content_type, "text/html" | "application/xhtml+xml") {
        return true;
    }
    let head = String::from_utf8_lossy(&body[..body.len().min(512)]).to_ascii_lowercase();
    head.contains("<!doctype html") || head.contains("<html")
}

fn is_text(content_type: &str) -> bool {
    content_type.starts_with("text/")
        || matches!(
            content_type,
            "application/json"
                | "application/ld+json"
                | "application/xml"
                | "application/rss+xml"
                | "application/atom+xml"
        )
}

fn text_format(content_type: &str) -> &str {
    match content_type {
        "application/json" | "application/ld+json" => "json",
        "application/xml" | "application/rss+xml" | "application/atom+xml" | "text/xml" => "xml",
        "text/csv" => "csv",
        "text/markdown" => "markdown",
        _ => "text",
    }
}

fn render_text(content_type: &str, body: &[u8]) -> String {
    let text = String::from_utf8_lossy(body).into_owned();
    if matches!(content_type, "application/json" | "application/ld+json") {
        if let Ok(json) = serde_json::from_str::<Value>(&text) {
            return serde_json::to_string_pretty(&json).unwrap_or(text);
        }
    }
    text
}

fn format_from_content_type(content_type: &str) -> Option<Format> {
    match content_type {
        "application/pdf" => Some(Format::Pdf),
        "application/msword" => Some(Format::Doc),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        | "application/vnd.ms-word.document.macroenabled.12" => Some(Format::Docx),
        "application/vnd.ms-powerpoint" => Some(Format::Ppt),
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        | "application/vnd.ms-powerpoint.presentation.macroenabled.12" => Some(Format::Pptx),
        "application/vnd.ms-excel"
        | "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        | "application/vnd.ms-excel.sheet.macroenabled.12" => Some(Format::Excel),
        "application/vnd.oasis.opendocument.text" => Some(Format::Odt),
        "application/vnd.oasis.opendocument.spreadsheet" => Some(Format::Ods),
        "application/vnd.oasis.opendocument.presentation" => Some(Format::Odp),
        "application/rtf" | "text/rtf" => Some(Format::Rtf),
        "application/epub+zip" => Some(Format::Epub),
        "text/csv" => Some(Format::Csv),
        _ => None,
    }
}

fn format_name(format: Format) -> &'static str {
    match format {
        Format::Doc => "doc",
        Format::Docx => "docx",
        Format::Odt => "odt",
        Format::Pdf => "pdf",
        Format::Ppt => "ppt",
        Format::Pptx => "pptx",
        Format::Rtf => "rtf",
        Format::Epub => "epub",
        Format::Excel => "excel",
        Format::Ods => "ods",
        Format::Odp => "odp",
        Format::Csv => "csv",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input() -> ScrapeInput {
        ScrapeInput::from_value(Value::String("https://example.com".into())).unwrap()
    }

    #[test]
    fn accepts_a_bare_url_input() {
        let parsed = input();
        assert_eq!(parsed.url, "https://example.com");
        assert!(!parsed.include_images);
        assert!(parsed.if_none_match.is_none());
    }

    #[test]
    fn accepts_cache_validators_for_watch_clients() {
        let parsed = ScrapeInput::from_value(serde_json::json!({
            "url": "https://example.com",
            "if_none_match": "\"abc123\"",
            "if_modified_since": "Wed, 21 Oct 2015 07:28:00 GMT"
        }))
        .unwrap();
        assert_eq!(parsed.if_none_match.as_deref(), Some("\"abc123\""));
        assert!(parsed.if_modified_since.is_some());
    }

    #[test]
    fn extracts_main_html_and_metadata() {
        let html = br#"<!doctype html><html lang="en"><head><title>Useful title</title></head><body><nav>Noise</nav><article><h1>Useful title</h1><p>This is the useful article body with enough text to be retained by the extractor.</p></article></body></html>"#;
        let result = extract_html(
            &Url::parse("https://example.com/post").unwrap(),
            html,
            &input(),
        )
        .unwrap();
        assert!(result.markdown.contains("useful article body"));
        assert_eq!(
            result.metadata.unwrap().title.as_deref(),
            Some("Useful title")
        );
        assert_eq!(result.source, "firecrawl-html-extractor");
    }

    #[test]
    fn converts_named_csv_documents() {
        let result = extract_document(
            &Url::parse("https://example.com/report.csv").unwrap(),
            "text/csv",
            b"name,total\nAda,42\n",
        )
        .unwrap();
        assert_eq!(result.format, "csv");
        assert!(result.markdown.contains("Ada"));
    }
}

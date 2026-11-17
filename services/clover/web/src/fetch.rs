use crate::config::Config;
use futures_util::StreamExt;
use reqwest::{
    header::{
        HeaderValue, CONTENT_LENGTH, CONTENT_TYPE, ETAG, IF_MODIFIED_SINCE, IF_NONE_MATCH,
        LAST_MODIFIED, LOCATION,
    },
    redirect::Policy,
};
use std::{
    net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr},
    time::{Duration, Instant},
};
use tokio::net::lookup_host;
use url::{Host, Url};

#[derive(Debug)]
pub struct FetchResult {
    pub not_modified: bool,
    pub final_url: Url,
    pub status_code: u16,
    pub content_type: Option<String>,
    pub etag: Option<String>,
    pub last_modified: Option<String>,
    pub body: Vec<u8>,
    pub redirects: usize,
    pub duration_ms: f64,
}

#[derive(Debug)]
pub struct FetchError {
    pub code: &'static str,
    pub message: String,
}

impl FetchError {
    fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

pub async fn fetch(
    raw_url: &str,
    config: &Config,
    requested_max_bytes: Option<usize>,
    requested_timeout_ms: Option<u64>,
    if_none_match: Option<&str>,
    if_modified_since: Option<&str>,
) -> Result<FetchResult, FetchError> {
    let started = Instant::now();
    let mut current = Url::parse(raw_url)
        .map_err(|error| FetchError::new("invalid_url", format!("invalid URL: {error}")))?;
    let max_bytes = requested_max_bytes
        .unwrap_or(config.max_response_bytes)
        .min(config.max_response_bytes);
    if max_bytes == 0 {
        return Err(FetchError::new(
            "invalid_limit",
            "max_bytes must be greater than zero",
        ));
    }
    let timeout = Duration::from_millis(
        requested_timeout_ms
            .unwrap_or(config.request_timeout.as_millis() as u64)
            .min(config.request_timeout.as_millis() as u64)
            .max(1),
    );

    let original = current.clone();
    let if_none_match = validator_header(if_none_match, "If-None-Match")?;
    let if_modified_since = validator_header(if_modified_since, "If-Modified-Since")?;

    for redirects in 0..=config.max_redirects {
        let target = validate_and_resolve(&current, config).await?;
        let mut builder = reqwest::Client::builder()
            .no_proxy()
            .redirect(Policy::none())
            .timeout(timeout)
            .user_agent(&config.user_agent);
        if let Some((host, address)) = target.resolution {
            builder = builder.resolve(&host, address);
        }
        let client = builder
            .build()
            .map_err(|error| FetchError::new("client_error", error.to_string()))?;
        let mut request = client.get(current.clone());
        // Do not leak opaque validators to another origin after a redirect.
        if same_origin(&original, &current) {
            if let Some(value) = &if_none_match {
                request = request.header(IF_NONE_MATCH, value.clone());
            }
            if let Some(value) = &if_modified_since {
                request = request.header(IF_MODIFIED_SINCE, value.clone());
            }
        }
        let response = request
            .send()
            .await
            .map_err(|error| map_transport_error(error, timeout))?;

        // 304 is in the 3xx class but is a terminal cache-validation result,
        // not a redirect and therefore correctly has no Location header.
        if response.status() == reqwest::StatusCode::NOT_MODIFIED {
            return Ok(FetchResult {
                not_modified: true,
                final_url: current,
                status_code: response.status().as_u16(),
                content_type: header_text(&response, CONTENT_TYPE),
                etag: header_text(&response, ETAG),
                last_modified: header_text(&response, LAST_MODIFIED),
                body: Vec::new(),
                redirects,
                duration_ms: started.elapsed().as_secs_f64() * 1000.0,
            });
        }

        if response.status().is_redirection() {
            if redirects == config.max_redirects {
                return Err(FetchError::new(
                    "too_many_redirects",
                    format!("redirect limit ({}) exceeded", config.max_redirects),
                ));
            }
            let location = response
                .headers()
                .get(LOCATION)
                .ok_or_else(|| {
                    FetchError::new("bad_redirect", "redirect response has no Location header")
                })?
                .to_str()
                .map_err(|_| {
                    FetchError::new("bad_redirect", "redirect Location is not valid text")
                })?;
            current = current.join(location).map_err(|error| {
                FetchError::new("bad_redirect", format!("invalid redirect target: {error}"))
            })?;
            continue;
        }

        if !response.status().is_success() {
            return Err(FetchError::new(
                "http_status",
                format!("upstream returned HTTP {}", response.status().as_u16()),
            ));
        }

        if let Some(length) = response
            .headers()
            .get(CONTENT_LENGTH)
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.parse::<usize>().ok())
        {
            if length > max_bytes {
                return Err(FetchError::new(
                    "response_too_large",
                    format!("declared response size {length} exceeds the {max_bytes}-byte limit"),
                ));
            }
        }

        let status_code = response.status().as_u16();
        let content_type = header_text(&response, CONTENT_TYPE);
        let etag = header_text(&response, ETAG);
        let last_modified = header_text(&response, LAST_MODIFIED);
        let mut stream = response.bytes_stream();
        let mut body = Vec::new();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|error| map_transport_error(error, timeout))?;
            if body.len().saturating_add(chunk.len()) > max_bytes {
                return Err(FetchError::new(
                    "response_too_large",
                    format!("response exceeded the {max_bytes}-byte limit while streaming"),
                ));
            }
            body.extend_from_slice(&chunk);
        }

        return Ok(FetchResult {
            not_modified: false,
            final_url: current,
            status_code,
            content_type,
            etag,
            last_modified,
            body,
            redirects,
            duration_ms: started.elapsed().as_secs_f64() * 1000.0,
        });
    }

    Err(FetchError::new(
        "too_many_redirects",
        "redirect limit exceeded",
    ))
}

fn validator_header(value: Option<&str>, name: &str) -> Result<Option<HeaderValue>, FetchError> {
    let Some(value) = value.map(str::trim).filter(|value| !value.is_empty()) else {
        return Ok(None);
    };
    if value.len() > 1_024 {
        return Err(FetchError::new(
            "invalid_validator",
            format!("{name} exceeds the 1024-byte limit"),
        ));
    }
    HeaderValue::from_str(value).map(Some).map_err(|_| {
        FetchError::new(
            "invalid_validator",
            format!("{name} is not a valid HTTP header value"),
        )
    })
}

fn same_origin(left: &Url, right: &Url) -> bool {
    left.scheme() == right.scheme()
        && left.host_str().map(str::to_ascii_lowercase)
            == right.host_str().map(str::to_ascii_lowercase)
        && left.port_or_known_default() == right.port_or_known_default()
}

fn header_text(response: &reqwest::Response, name: reqwest::header::HeaderName) -> Option<String> {
    response
        .headers()
        .get(name)
        .and_then(|value| value.to_str().ok())
        .map(str::to_string)
}

fn map_transport_error(error: reqwest::Error, timeout: Duration) -> FetchError {
    if error.is_timeout() {
        FetchError::new(
            "timeout",
            format!("request exceeded the {}ms timeout", timeout.as_millis()),
        )
    } else {
        FetchError::new("transport", error.to_string())
    }
}

struct ValidatedTarget {
    resolution: Option<(String, SocketAddr)>,
}

async fn validate_and_resolve(url: &Url, config: &Config) -> Result<ValidatedTarget, FetchError> {
    validate_url_shape(url, config)?;
    let host = url
        .host()
        .ok_or_else(|| FetchError::new("invalid_url", "URL has no host"))?;
    let port = url
        .port_or_known_default()
        .ok_or_else(|| FetchError::new("invalid_url", "URL has no usable port"))?;

    match host {
        Host::Ipv4(address) => {
            reject_forbidden_ip(IpAddr::V4(address))?;
            Ok(ValidatedTarget { resolution: None })
        }
        Host::Ipv6(address) => {
            reject_forbidden_ip(IpAddr::V6(address))?;
            Ok(ValidatedTarget { resolution: None })
        }
        Host::Domain(domain) => {
            let addresses = lookup_host((domain, port))
                .await
                .map_err(|error| {
                    FetchError::new("dns_failure", format!("DNS lookup failed: {error}"))
                })?
                .collect::<Vec<_>>();
            if addresses.is_empty() {
                return Err(FetchError::new(
                    "dns_failure",
                    "DNS lookup returned no addresses",
                ));
            }
            for address in &addresses {
                reject_forbidden_ip(address.ip())?;
            }
            Ok(ValidatedTarget {
                resolution: Some((domain.to_string(), addresses[0])),
            })
        }
    }
}

fn validate_url_shape(url: &Url, config: &Config) -> Result<(), FetchError> {
    if !matches!(url.scheme(), "http" | "https") {
        return Err(FetchError::new(
            "unsupported_scheme",
            "only http and https URLs are supported",
        ));
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err(FetchError::new(
            "credentials_not_allowed",
            "credentials embedded in URLs are not allowed",
        ));
    }
    let host = url
        .host_str()
        .ok_or_else(|| FetchError::new("invalid_url", "URL has no host"))?
        .trim_end_matches('.')
        .to_ascii_lowercase();
    if is_forbidden_hostname(&host) {
        return Err(FetchError::new(
            "blocked_target",
            "local and metadata hostnames are blocked",
        ));
    }
    match url.host() {
        Some(Host::Ipv4(address)) => reject_forbidden_ip(IpAddr::V4(address))?,
        Some(Host::Ipv6(address)) => reject_forbidden_ip(IpAddr::V6(address))?,
        _ => {}
    }
    let port = url
        .port_or_known_default()
        .ok_or_else(|| FetchError::new("invalid_url", "URL has no usable port"))?;
    if !config.allowed_ports.contains(&port) {
        return Err(FetchError::new(
            "port_not_allowed",
            format!("port {port} is not in the server allowlist"),
        ));
    }
    Ok(())
}

fn is_forbidden_hostname(host: &str) -> bool {
    host == "localhost"
        || host == "metadata"
        || host == "metadata.google.internal"
        || host.ends_with(".localhost")
        || host.ends_with(".local")
        || host.ends_with(".internal")
        || host.ends_with(".home.arpa")
}

fn reject_forbidden_ip(ip: IpAddr) -> Result<(), FetchError> {
    if is_forbidden_ip(ip) {
        Err(FetchError::new(
            "blocked_target",
            "the target resolves to a private, local, or reserved address",
        ))
    } else {
        Ok(())
    }
}

fn is_forbidden_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(ip) => is_forbidden_ipv4(ip),
        IpAddr::V6(ip) => {
            if let Some(mapped) = ip.to_ipv4_mapped() {
                return is_forbidden_ipv4(mapped);
            }
            ip.is_loopback()
                || ip.is_unspecified()
                || ip.is_multicast()
                || ip.is_unique_local()
                || ip.is_unicast_link_local()
                || is_ipv6_documentation(ip)
        }
    }
}

fn is_forbidden_ipv4(ip: Ipv4Addr) -> bool {
    let [a, b, c, _] = ip.octets();
    ip.is_private()
        || ip.is_loopback()
        || ip.is_link_local()
        || ip.is_broadcast()
        || ip.is_documentation()
        || ip.is_unspecified()
        || ip.is_multicast()
        || a == 0
        || a >= 240
        || (a == 100 && (64..=127).contains(&b))
        || (a == 192 && b == 0 && c == 0)
        || (a == 198 && matches!(b, 18 | 19))
}

fn is_ipv6_documentation(ip: Ipv6Addr) -> bool {
    let segments = ip.segments();
    segments[0] == 0x2001 && segments[1] == 0x0db8
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    fn config() -> Config {
        Config {
            bind: "127.0.0.1:8091".parse().unwrap(),
            max_inputs: 8,
            max_response_bytes: 1024,
            request_timeout: Duration::from_secs(1),
            max_redirects: 3,
            max_concurrency: 2,
            allowed_ports: HashSet::from([80, 443]),
            user_agent: "test".into(),
        }
    }

    #[test]
    fn blocks_private_metadata_and_non_web_targets() {
        for raw in [
            "http://127.0.0.1/",
            "http://10.1.2.3/",
            "http://169.254.169.254/computeMetadata/v1/",
            "http://[::1]/",
            "http://metadata.google.internal/",
            "file:///etc/passwd",
            "http://example.com:22/",
            "http://user:pass@example.com/",
        ] {
            let url = Url::parse(raw).unwrap();
            let shape = validate_url_shape(&url, &config());
            if shape.is_ok() {
                let ip = url.host_str().and_then(|host| host.parse::<IpAddr>().ok());
                assert!(
                    ip.is_some_and(is_forbidden_ip),
                    "expected {raw} to be blocked"
                );
            }
        }
    }

    #[test]
    fn accepts_normal_public_web_targets() {
        for raw in ["https://example.com/docs", "http://93.184.216.34/"] {
            let url = Url::parse(raw).unwrap();
            validate_url_shape(&url, &config()).unwrap();
            if let Some(ip) = url.host_str().and_then(|host| host.parse::<IpAddr>().ok()) {
                assert!(!is_forbidden_ip(ip));
            }
        }
    }

    #[test]
    fn validates_conditional_request_headers() {
        assert!(validator_header(Some("\"abc123\""), "If-None-Match")
            .unwrap()
            .is_some());
        assert!(
            validator_header(Some("Wed, 21 Oct 2015 07:28:00 GMT"), "If-Modified-Since")
                .unwrap()
                .is_some()
        );
        assert!(validator_header(Some("bad\nvalue"), "If-None-Match").is_err());
    }

    #[test]
    fn validators_are_not_forwarded_across_origins() {
        let original = Url::parse("https://example.com/start").unwrap();
        assert!(same_origin(
            &original,
            &Url::parse("https://example.com/next").unwrap()
        ));
        assert!(!same_origin(
            &original,
            &Url::parse("https://cdn.example.com/next").unwrap()
        ));
    }
}

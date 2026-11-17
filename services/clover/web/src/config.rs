use std::{collections::HashSet, env, net::SocketAddr, time::Duration};

#[derive(Clone, Debug)]
pub struct Config {
    pub bind: SocketAddr,
    pub max_inputs: usize,
    pub max_response_bytes: usize,
    pub request_timeout: Duration,
    pub max_redirects: usize,
    pub max_concurrency: usize,
    pub allowed_ports: HashSet<u16>,
    pub user_agent: String,
}

impl Config {
    pub fn from_env() -> Result<Self, String> {
        let bind = value("CLOVER_WEB_BIND", "127.0.0.1:8091")?
            .parse()
            .map_err(|error| format!("bad CLOVER_WEB_BIND: {error}"))?;
        let max_inputs = positive_usize("CLOVER_WEB_MAX_INPUTS", 8)?;
        let max_response_bytes = positive_usize("CLOVER_WEB_MAX_RESPONSE_BYTES", 16 * 1024 * 1024)?;
        let request_timeout = Duration::from_millis(positive_u64("CLOVER_WEB_TIMEOUT_MS", 20_000)?);
        let max_redirects = positive_usize("CLOVER_WEB_MAX_REDIRECTS", 5)?;
        let max_concurrency = positive_usize("CLOVER_WEB_MAX_CONCURRENCY", 4)?;
        let allowed_ports = value("CLOVER_WEB_ALLOWED_PORTS", "80,443")?
            .split(',')
            .map(str::trim)
            .filter(|part| !part.is_empty())
            .map(|part| {
                part.parse::<u16>().map_err(|error| {
                    format!("bad port '{part}' in CLOVER_WEB_ALLOWED_PORTS: {error}")
                })
            })
            .collect::<Result<HashSet<_>, _>>()?;
        if allowed_ports.is_empty() {
            return Err("CLOVER_WEB_ALLOWED_PORTS must contain at least one port".into());
        }

        Ok(Self {
            bind,
            max_inputs,
            max_response_bytes,
            request_timeout,
            max_redirects,
            max_concurrency,
            allowed_ports,
            user_agent: value("CLOVER_WEB_USER_AGENT", "CloverWeb/0.2 (+https://github.com/ryrobes/calliope)")?,
        })
    }
}

fn value(name: &str, default: &str) -> Result<String, String> {
    match env::var(name) {
        Ok(value) if value.trim().is_empty() => Err(format!("{name} cannot be blank")),
        Ok(value) => Ok(value),
        Err(env::VarError::NotPresent) => Ok(default.to_string()),
        Err(error) => Err(format!("cannot read {name}: {error}")),
    }
}

fn positive_usize(name: &str, default: usize) -> Result<usize, String> {
    let value = value(name, &default.to_string())?
        .parse::<usize>()
        .map_err(|error| format!("bad {name}: {error}"))?;
    if value == 0 {
        return Err(format!("{name} must be greater than zero"));
    }
    Ok(value)
}

fn positive_u64(name: &str, default: u64) -> Result<u64, String> {
    let value = value(name, &default.to_string())?
        .parse::<u64>()
        .map_err(|error| format!("bad {name}: {error}"))?;
    if value == 0 {
        return Err(format!("{name} must be greater than zero"));
    }
    Ok(value)
}

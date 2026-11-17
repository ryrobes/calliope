//! OS handoffs carry a destination only. Authentication and access stay on the server.
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct Destination {
    pub server: String,
    pub app: Option<String>,
    pub record: Option<String>,
    pub id: Option<String>,
    /// A conversation to open (a "Callie is waiting" notification).
    #[serde(default)]
    pub session: Option<String>,
}
pub fn parse(value: &str) -> Result<Option<Destination>> {
    ensure!(value.len() <= 4096, "App link is too long");
    let url = reqwest::Url::parse(value)?;
    ensure!(
        url.scheme() == "calliope"
            && url.username().is_empty()
            && url.password().is_none()
            && url.port().is_none()
            && url.fragment().is_none(),
        "Invalid Calliope link"
    );
    if url.host_str() == Some("auth") && url.path() == "/complete" && url.query().is_none() {
        return Ok(None);
    }
    ensure!(
        url.host_str() == Some("open") && matches!(url.path(), "" | "/"),
        "Unknown Calliope link"
    );
    let mut fields = std::collections::HashMap::new();
    for (key, value) in url.query_pairs() {
        ensure!(
            ["server", "app", "record", "id", "session"].contains(&key.as_ref())
                && !fields.contains_key(key.as_ref()),
            "Invalid app link fields"
        );
        fields.insert(key.into_owned(), value.into_owned());
    }
    let server = reqwest::Url::parse(
        fields
            .get("server")
            .ok_or_else(|| anyhow::anyhow!("Missing Calliope server"))?,
    )?;
    ensure!(
        server.scheme() == "https"
            && server.host_str().is_some()
            && server.username().is_empty()
            && server.password().is_none()
            && matches!(server.path(), "" | "/")
            && server.query().is_none()
            && server.fragment().is_none(),
        "App links require an HTTPS instance address"
    );
    for key in ["app", "record"] {
        if let Some(value) = fields.get(key) {
            ensure!(
                !value.is_empty()
                    && value.len() <= 160
                    && value
                        .bytes()
                        .all(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-'),
                "Invalid app or record name"
            );
        }
    }
    if let Some(value) = fields.get("id") {
        ensure!(
            !value.is_empty() && value.len() <= 512 && !value.chars().any(char::is_control),
            "Invalid record id"
        );
    }
    if let Some(value) = fields.get("session") {
        ensure!(
            uuid::Uuid::parse_str(value).is_ok() && !fields.contains_key("app"),
            "Invalid conversation link"
        );
    }
    ensure!(
        fields.contains_key("record") == fields.contains_key("id")
            && (!fields.contains_key("record") || fields.contains_key("app")),
        "Incomplete record link"
    );
    Ok(Some(Destination {
        server: server.origin().ascii_serialization(),
        app: fields.remove("app"),
        record: fields.remove("record"),
        id: fields.remove("id"),
        session: fields.remove("session"),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn conversation_links_carry_a_session_only() {
        let link = parse("calliope://open?server=https%3A%2F%2Fcallie.example&session=6468e871-e6ad-4f67-a004-66ddb7a922e9")
            .unwrap()
            .unwrap();
        assert_eq!(link.session.as_deref(), Some("6468e871-e6ad-4f67-a004-66ddb7a922e9"));
        assert!(parse("calliope://open?server=https%3A%2F%2Fcallie.example&session=../x").is_err());
        assert!(parse("calliope://open?server=https%3A%2F%2Fcallie.example&app=a&session=6468e871-e6ad-4f67-a004-66ddb7a922e9").is_err());
    }
    #[test]
    fn record_link_and_completion_have_no_authority() {
        let link = parse(
            "calliope://open?server=https%3A%2F%2Fcallie.example&app=visits&record=visit&id=A%202",
        )
        .unwrap()
        .unwrap();
        assert_eq!(link.server, "https://callie.example");
        assert_eq!(link.id.as_deref(), Some("A 2"));
        assert!(parse("calliope://auth/complete").unwrap().is_none());
        for bad in [
            "calliope://auth/complete?cookie=secret",
            "calliope://open?server=http://evil.example",
            "calliope://open?server=https://user:pass@host",
            "calliope://open?server=https://host/path",
            "calliope://open?server=https://host&server=https://other",
            "calliope://open?server=https://host&record=visit&id=1",
            "calliope://open?server=https://host&app=../x",
            "https://host",
        ] {
            assert!(parse(bad).is_err(), "{bad}");
        }
    }
}

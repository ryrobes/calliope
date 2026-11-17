//! Streaming inference needs an inactivity deadline, not the short JSON deadline.
use futures_util::{Stream, StreamExt};
use serde::Deserialize;
use std::time::Duration;

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct StreamTimeouts {
    pub idle_ms: u64,
    pub total_ms: u64,
}
impl Default for StreamTimeouts {
    fn default() -> Self {
        Self {
            idle_ms: 180_000,
            total_ms: 900_000,
        }
    }
}

pub async fn send(
    request: reqwest::RequestBuilder,
    stream: bool,
    headers_ms: u64,
    limits: &StreamTimeouts,
) -> Result<reqwest::Response, String> {
    let total_ms = if stream { limits.total_ms } else { headers_ms };
    // reqwest's deadline includes the response BODY. Keep the original deadline
    // for JSON, but let streams use their own bounded lifetime.
    tokio::time::timeout(
        Duration::from_millis(headers_ms),
        request.timeout(Duration::from_millis(total_ms)).send(),
    )
    .await
    .map_err(|_| "upstream response headers timed out".to_string())?
    .map_err(|e| e.to_string())
}

pub async fn next<S: Stream + Unpin>(
    stream: &mut S,
    idle_ms: u64,
) -> Result<Option<S::Item>, tokio::time::error::Elapsed> {
    tokio::time::timeout(Duration::from_millis(idle_ms), stream.next()).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{body::Body, routing::get, Router};
    async fn server() -> (String, tokio::task::JoinHandle<()>) {
        let router = Router::new()
            .route(
                "/active",
                get(|| async {
                    Body::from_stream(async_stream::stream! {
                        for _ in 0..12 {
                            yield Ok::<_, std::io::Error>("data: chunk\n\n");
                            tokio::time::sleep(Duration::from_millis(50)).await;
                        }
                    })
                }),
            )
            .route(
                "/idle",
                get(|| async {
                    Body::from_stream(async_stream::stream! {
                        yield Ok::<_, std::io::Error>("data: first\n\n");
                        tokio::time::sleep(Duration::from_millis(500)).await;
                        yield Ok::<_, std::io::Error>("data: late\n\n");
                    })
                }),
            )
            .route(
                "/headers",
                get(|| async {
                    tokio::time::sleep(Duration::from_millis(500)).await;
                    "late headers"
                }),
            );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
        (url, task)
    }
    #[tokio::test]
    async fn active_stream_outlives_json_deadline_but_json_stays_bounded() {
        let (url, task) = server().await;
        let client = reqwest::Client::new();
        let limits = StreamTimeouts {
            idle_ms: 250,
            total_ms: 1500,
        };
        let response = send(client.get(format!("{url}/active")), true, 250, &limits)
            .await
            .unwrap();
        let mut body = response.bytes_stream();
        let mut count = 0;
        while let Some(chunk) = next(&mut body, limits.idle_ms).await.unwrap() {
            chunk.unwrap();
            count += 1;
        }
        assert!(count > 1);
        let response = send(client.get(format!("{url}/active")), false, 250, &limits)
            .await
            .unwrap();
        assert!(response.bytes().await.unwrap_err().is_timeout());
        task.abort();
    }
    #[tokio::test]
    async fn idle_and_header_timeouts_are_bounded() {
        let (url, task) = server().await;
        let client = reqwest::Client::new();
        let limits = StreamTimeouts {
            idle_ms: 100,
            total_ms: 1500,
        };
        let response = send(client.get(format!("{url}/idle")), true, 250, &limits)
            .await
            .unwrap();
        let mut body = response.bytes_stream();
        assert!(next(&mut body, limits.idle_ms)
            .await
            .unwrap()
            .unwrap()
            .is_ok());
        assert!(next(&mut body, limits.idle_ms).await.is_err());
        assert!(
            send(client.get(format!("{url}/headers")), true, 100, &limits)
                .await
                .unwrap_err()
                .contains("headers timed out")
        );
        task.abort();
    }
    #[tokio::test]
    async fn active_stream_still_has_total_deadline() {
        let (url, task) = server().await;
        let limits = StreamTimeouts {
            idle_ms: 250,
            total_ms: 200,
        };
        let response = send(
            reqwest::Client::new().get(format!("{url}/active")),
            true,
            250,
            &limits,
        )
        .await
        .unwrap();
        assert!(response.bytes().await.unwrap_err().is_timeout());
        task.abort();
    }
}

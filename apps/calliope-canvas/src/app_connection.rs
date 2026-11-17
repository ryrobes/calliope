//! Read recovery policy. This never schedules business commands.
#[derive(Default)]
pub struct Recovery {
    pub foreground: bool,
    pub offline: bool,
    pub blocked: bool,
    pub attempts: u32,
    pub next_read: u64,
}
impl Recovery {
    pub fn foreground(&mut self, value: bool, now: u64) -> bool {
        let resumed = value && !self.foreground;
        self.foreground = value;
        if resumed && !self.blocked {
            self.next_read = now;
        }
        resumed
    }
    pub fn success(&mut self, now: u64) {
        self.offline = false;
        self.blocked = false;
        self.attempts = 0;
        self.next_read = now + 15_000;
    }
    pub fn failed(&mut self, now: u64, retryable: bool) {
        self.offline = retryable;
        self.blocked = !retryable;
        self.attempts = self.attempts.saturating_add(1);
        self.next_read =
            now + (5_000u64 * (1u64 << self.attempts.saturating_sub(1).min(4))).min(60_000);
    }
    pub fn due(&self, now: u64) -> bool {
        self.foreground && !self.blocked && now >= self.next_read
    }
}
pub fn retryable(error: &anyhow::Error) -> bool {
    crate::app_drafts::transport_error(error)
        || error
            .downcast_ref::<crate::app_drafts::HttpError>()
            .is_some_and(|e| matches!(e.status, 500..=599))
}
pub fn timestamp() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
pub fn age(at: u64) -> String {
    if at == 0 {
        return "Cached view · refresh time unknown".into();
    }
    let seconds = timestamp().saturating_sub(at);
    if seconds < 60 {
        "Last server response just now".into()
    } else if seconds < 3600 {
        format!("Last server response {} min ago", seconds / 60)
    } else {
        format!("Last server response {} h ago", seconds / 3600)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn retries_back_off_and_resume_only_schedules_reads() {
        let mut r = Recovery::default();
        assert!(r.foreground(true, 100));
        assert!(r.due(100));
        r.failed(100, true);
        assert!(!r.due(5099));
        assert!(r.due(5100));
        r.failed(5100, true);
        assert_eq!(r.next_read, 15100);
        r.foreground(false, 6000);
        assert!(!r.due(90000));
        assert!(r.foreground(true, 90001));
        assert!(r.due(90001));
        r.success(90001);
        assert!(!r.offline);
        assert!(!r.due(90002));
    }
    #[test]
    fn auth_or_contract_failure_is_not_an_offline_retry() {
        let mut r = Recovery::default();
        r.foreground(true, 0);
        r.failed(1, false);
        assert!(!r.offline);
        assert!(!r.due(999999));
        r.foreground(false, 2);
        r.foreground(true, 3);
        assert!(!r.due(4));
    }
}

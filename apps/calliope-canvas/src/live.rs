//! One read per board query source, shared across cells and placements.
use crate::board::QuerySource;
use serde_json::Value;
use std::{
    collections::{HashMap, VecDeque},
    time::{Duration, Instant},
};

#[derive(Default)]
pub struct Reading {
    pub value: Option<Value>,
    pub error: Option<String>,
    pub pending: Option<u64>,
    attempted: Option<Instant>,
}

#[derive(Default)]
pub struct BoardReads {
    pub readings: HashMap<String, Reading>,
    queued: VecDeque<QuerySource>,
    in_flight: HashMap<u64, String>,
    next_id: u64,
}

impl BoardReads {
    pub fn pending_count(&self) -> usize {
        self.in_flight.len()
    }

    pub fn enqueue(&mut self, source: QuerySource) {
        let key = source.key();
        if self.readings.get(&key).is_some_and(|r| r.pending.is_some())
            || self.queued.iter().any(|q| q.key() == key)
        {
            return;
        }
        self.queued.push_back(source);
    }

    pub fn schedule(&mut self, source: QuerySource, auto: bool, now: Instant) {
        let due = self.readings.get(&source.key()).is_none_or(|r| {
            r.pending.is_none()
                && r.attempted
                    .is_none_or(|at| auto && now.duration_since(at) >= Duration::from_secs(60))
        });
        if due {
            self.enqueue(source);
        }
    }

    pub fn begin_next(
        &mut self,
        policies: &HashMap<String, bool>,
        now: Instant,
    ) -> Option<(u64, QuerySource)> {
        while let Some(source) = self.queued.pop_front() {
            let key = source.key();
            if !policies.contains_key(&key) {
                continue;
            }
            self.next_id += 1;
            let id = self.next_id;
            let reading = self.readings.entry(key.clone()).or_default();
            reading.pending = Some(id);
            reading.attempted = Some(now);
            // Keep the prior error and value visible while retrying.
            self.in_flight.insert(id, key);
            return Some((id, source));
        }
        None
    }

    pub fn complete(&mut self, id: u64, result: Result<Value, String>) {
        let Some(key) = self.in_flight.remove(&id) else {
            return;
        };
        let Some(reading) = self
            .readings
            .get_mut(&key)
            .filter(|r| r.pending == Some(id))
        else {
            return;
        };
        reading.pending = None;
        match result {
            Ok(value) => {
                reading.value = Some(value);
                reading.error = None;
            }
            Err(error) => reading.error = Some(error),
        }
    }

    pub fn use_saved(&mut self, key: &str) {
        self.queued.retain(|q| q.key() != key);
        self.readings.remove(key);
        // Keep outstanding work in the concurrency count. Its late result is
        // discarded even if the user re-enables this source before it returns.
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn source() -> QuerySource {
        QuerySource {
            session_id: "chat".into(),
            surface_id: "query-v1".into(),
        }
    }

    #[test]
    fn shared_reads_deduplicate_and_manual_failures_do_not_poll() {
        let mut reads = BoardReads::default();
        let s = source();
        let key = s.key();
        let now = Instant::now();
        let policies = HashMap::from([(key.clone(), false)]);
        for _ in 0..3 {
            reads.schedule(s.clone(), false, now);
        }
        let (id, _) = reads.begin_next(&policies, now).unwrap();
        assert!(reads.begin_next(&policies, now).is_none());
        reads.complete(id, Ok(json!({"result":{"rows":[{"sales":10}]}})));
        reads.enqueue(s.clone());
        let (id, _) = reads.begin_next(&policies, now).unwrap();
        reads.complete(id, Err("Access revoked".into()));
        assert_eq!(
            reads.readings[&key].value.as_ref().unwrap()["result"]["rows"][0]["sales"],
            10
        );
        assert_eq!(
            reads.readings[&key].error.as_deref(),
            Some("Access revoked")
        );
        reads.schedule(s.clone(), false, now + Duration::from_secs(61));
        assert!(reads.begin_next(&policies, now).is_none());
        reads.schedule(s.clone(), true, now + Duration::from_secs(59));
        assert!(reads.begin_next(&policies, now).is_none());
        reads.schedule(s, true, now + Duration::from_secs(61));
        assert!(
            reads
                .begin_next(&policies, now + Duration::from_secs(61))
                .is_some()
        );
    }

    #[test]
    fn returning_to_saved_cancels_queue_and_ignores_late_old_reads() {
        let mut reads = BoardReads::default();
        let s = source();
        let key = s.key();
        let now = Instant::now();
        let policies = HashMap::from([(key.clone(), true)]);
        reads.enqueue(s.clone());
        let (old, _) = reads.begin_next(&policies, now).unwrap();
        reads.use_saved(&key);
        assert_eq!(reads.pending_count(), 1);
        reads.enqueue(s.clone());
        let (new, _) = reads.begin_next(&policies, now).unwrap();
        reads.complete(old, Ok(json!({"old":true})));
        assert!(reads.readings[&key].value.is_none());
        assert_eq!(reads.readings[&key].pending, Some(new));
        reads.complete(new, Ok(json!({"new":true})));
        assert_eq!(reads.readings[&key].value, Some(json!({"new":true})));
        assert_eq!(reads.pending_count(), 0);
        reads.enqueue(s);
        reads.use_saved(&key);
        assert!(reads.begin_next(&policies, now).is_none());
    }
}

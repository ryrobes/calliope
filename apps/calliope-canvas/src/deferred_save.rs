//! One ordered writer with one replaceable pending snapshot. Serialization and
//! fsync happen here, never in a scroll/key handler. Normal close drains it.
use std::{
    sync::{Arc, Condvar, Mutex},
    thread::JoinHandle,
    time::{Duration, Instant},
};

struct Pending<T> {
    value: Option<T>,
    first: Instant,
    last: Instant,
    closing: bool,
    writing: bool,
    flushing: bool,
}
pub struct DeferredSave<T> {
    shared: Arc<(Mutex<Pending<T>>, Condvar)>,
    worker: Option<JoinHandle<()>>,
}
impl<T: Send + 'static> DeferredSave<T> {
    pub fn new(mut write: impl FnMut(T) + Send + 'static) -> Self {
        let shared = Arc::new((
            Mutex::new(Pending {
                value: None,
                first: Instant::now(),
                last: Instant::now(),
                closing: false,
                writing: false,
                flushing: false,
            }),
            Condvar::new(),
        ));
        let worker_state = shared.clone();
        let worker = std::thread::spawn(move || {
            let (lock, wake) = &*worker_state;
            loop {
                let mut state = lock.lock().unwrap();
                while state.value.is_none() && !state.closing {
                    state = wake.wait(state).unwrap();
                }
                if state.value.is_none() {
                    break;
                }
                // Coalesce a gesture, but checkpoint long gestures every second.
                let due = (state.last + Duration::from_millis(250))
                    .min(state.first + Duration::from_secs(1));
                if !state.closing && !state.flushing && due > Instant::now() {
                    let _ = wake
                        .wait_timeout(state, due.saturating_duration_since(Instant::now()))
                        .unwrap();
                    continue;
                }
                let value = state.value.take().unwrap();
                state.writing = true;
                drop(state);
                write(value);
                lock.lock().unwrap().writing = false;
                wake.notify_all();
            }
        });
        Self {
            shared,
            worker: Some(worker),
        }
    }
    pub fn submit(&self, value: T) {
        let (lock, wake) = &*self.shared;
        let mut state = lock.lock().unwrap();
        if state.value.is_none() {
            state.first = Instant::now();
        }
        state.last = Instant::now();
        state.value = Some(value);
        wake.notify_one();
    }
    /// Used only at an explicit close/quit boundary. Normal interaction never waits.
    pub fn flush(&self) {
        let (lock, wake) = &*self.shared;
        let mut state = lock.lock().unwrap();
        state.flushing = true;
        wake.notify_all();
        while state.value.is_some() || state.writing {
            state = wake.wait(state).unwrap();
        }
        state.flushing = false;
    }
}
impl<T> Drop for DeferredSave<T> {
    fn drop(&mut self) {
        let (lock, wake) = &*self.shared;
        lock.lock().unwrap().closing = true;
        wake.notify_one();
        // Only shutdown waits. This also handles closing the last GPUI window,
        // where an entity-scoped on_app_quit handler may already be gone.
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;
    #[test]
    fn burst_is_coalesced_and_close_flushes_latest_snapshot() {
        let (tx, rx) = mpsc::channel();
        let writer = DeferredSave::new(move |v| tx.send(v).unwrap());
        for n in 0..100 {
            writer.submit(n);
        }
        drop(writer);
        assert_eq!(rx.into_iter().collect::<Vec<_>>(), vec![99]);
    }
    #[test]
    fn newer_snapshot_follows_an_inflight_write() {
        let (started_tx, started_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let (done_tx, done_rx) = mpsc::channel();
        let writer = DeferredSave::new(move |v| {
            if v == 1 {
                started_tx.send(()).unwrap();
                release_rx.recv().unwrap();
            }
            done_tx.send(v).unwrap();
        });
        writer.submit(1);
        started_rx.recv_timeout(Duration::from_secs(3)).unwrap();
        writer.submit(2);
        writer.submit(3);
        release_tx.send(()).unwrap();
        drop(writer);
        assert_eq!(done_rx.into_iter().collect::<Vec<_>>(), vec![1, 3]);
    }
    #[test]
    fn flush_finishes_pending_save_without_closing_writer() {
        let (tx, rx) = mpsc::channel();
        let writer = DeferredSave::new(move |v| tx.send(v).unwrap());
        writer.submit(1);
        writer.flush();
        assert_eq!(rx.try_recv().unwrap(), 1);
        writer.submit(2);
        writer.flush();
        assert_eq!(rx.try_recv().unwrap(), 2);
    }
}

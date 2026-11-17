//! Worker results wake GPUI immediately. The bounded signal coalesces wakeups;
//! the ordinary channel retains every result, in order.
use std::sync::mpsc;

pub struct Sender<T> {
    values: mpsc::Sender<T>,
    wake: smol::channel::Sender<()>,
}
impl<T> Clone for Sender<T> {
    fn clone(&self) -> Self {
        Self {
            values: self.values.clone(),
            wake: self.wake.clone(),
        }
    }
}
impl<T> Sender<T> {
    pub fn send(&self, value: T) -> Result<(), mpsc::SendError<T>> {
        self.values.send(value)?;
        self.wake();
        Ok(())
    }
    pub fn wake(&self) {
        let _ = self.wake.try_send(());
    }
    pub fn notifier(&self) -> std::sync::Arc<dyn Fn() + Send + Sync> {
        let wake = self.wake.clone();
        std::sync::Arc::new(move || {
            let _ = wake.try_send(());
        })
    }
}
pub fn channel<T>() -> (Sender<T>, mpsc::Receiver<T>, smol::channel::Receiver<()>) {
    let (values, rx) = mpsc::channel();
    let (wake, events) = smol::channel::bounded(1);
    (Sender { values, wake }, rx, events)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn coalesced_wake_keeps_every_result_and_wakes_again_after_drain() {
        let (tx, rx, wake) = channel();
        for n in 0..100 {
            tx.send(n).unwrap();
        }
        assert_eq!(wake.len(), 1);
        smol::block_on(wake.recv()).unwrap();
        assert_eq!(
            rx.try_iter().collect::<Vec<_>>(),
            (0..100).collect::<Vec<_>>()
        );
        tx.send(100).unwrap();
        smol::block_on(wake.recv()).unwrap();
        assert_eq!(rx.try_recv().unwrap(), 100);
    }
}

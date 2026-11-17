//! Native audio I/O with Calliope's existing web-client contracts.
//! Provider selection, credentials, rewriting and synthesis remain on the Calliope server.
use crate::{api::Api, model::string};
use anyhow::{Context, Result, bail};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::{
    io::{BufRead, BufReader, Read, Write},
    process::{Child, Command, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicU8, Ordering},
        mpsc::{self, Receiver, Sender},
    },
    time::Duration,
};

#[derive(Clone, Debug)]
pub struct Settings {
    pub stt: bool,
    pub tts: bool,
    pub auto_read: bool,
    pub mode: String,
    pub personality: String,
    pub max_audio_bytes: usize,
    pub max_audio_seconds: usize,
    pub prepare_seconds: u64,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            stt: false,
            tts: false,
            auto_read: false,
            mode: "fast".into(),
            personality: String::new(),
            max_audio_bytes: 12 * 1024 * 1024,
            max_audio_seconds: 600,
            prepare_seconds: 120,
        }
    }
}

impl Settings {
    pub fn load(api: &Api) -> Result<Self> {
        let config = api.get("/api/calliope/config")?;
        let preferences = api.get("/api/calliope/response-preferences")?;
        let stt = &config["speech_to_text"];
        let tts = &config["text_to_speech"];
        let prefs = &preferences["preferences"];
        let mode = string(prefs, "read_aloud_mode");
        Ok(Self {
            stt: stt["enabled"] == true,
            tts: tts["enabled"] == true,
            auto_read: matches!(mode.as_str(), "fast" | "expressive"),
            mode: if mode == "expressive" {
                mode
            } else {
                "fast".into()
            },
            personality: string(prefs, "response_personality"),
            max_audio_bytes: stt["max_audio_bytes"]
                .as_u64()
                .unwrap_or(12 * 1024 * 1024)
                .min(24 * 1024 * 1024) as usize,
            max_audio_seconds: stt["max_audio_seconds"]
                .as_u64()
                .unwrap_or(600)
                .clamp(1, 600) as usize,
            prepare_seconds: tts["prepare_timeout_seconds"]
                .as_u64()
                .unwrap_or(120)
                .clamp(10, 300),
        })
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Phase {
    Idle,
    Recording,
    Transcribing,
    Preparing,
    Playing,
}

pub enum Event {
    Phase(Phase),
    Level(f32),
    Transcript(String),
    Script(String),
    Played(f64),
    Finished,
    Error(String),
}

struct AudioTask {
    #[cfg(any(target_os="android", target_os="ios"))]
    id: i64,
    // 0: running; 1: finish dictation; 2: discard/stop.
    stop: AtomicU8,
    child: Mutex<Option<Child>>,
}

impl Default for AudioTask {
    fn default()->Self {
        #[cfg(any(target_os="android", target_os="ios"))]
        static NEXT: std::sync::atomic::AtomicI64=std::sync::atomic::AtomicI64::new(1);
        Self {stop:AtomicU8::new(0),child:Mutex::new(None),#[cfg(any(target_os="android", target_os="ios"))] id:NEXT.fetch_add(1,Ordering::Relaxed)}
    }
}

impl AudioTask {
    fn stop(&self, discard: bool) {
        self.stop
            .store(if discard { 2 } else { 1 }, Ordering::SeqCst);
        #[cfg(any(target_os="android", target_os="ios"))]
        crate::host::audio_stop(self.id,discard);
        if let Some(child) = self.child.lock().unwrap().as_mut() {
            let _ = child.kill();
        }
    }
    fn cancelled(&self) -> bool {
        self.stop.load(Ordering::SeqCst) == 2
    }
    fn install(&self, mut child: Child) -> Result<()> {
        let mut slot = self.child.lock().unwrap();
        if self.stop.load(Ordering::SeqCst) != 0 {
            let _ = child.kill();
            let _ = child.wait();
            bail!("Audio operation cancelled");
        }
        *slot = Some(child);
        Ok(())
    }
    fn reap(&self) {
        if let Some(mut child) = self.child.lock().unwrap().take() {
            let _ = child.wait();
        }
    }
}

pub struct Voice {
    pub phase: Phase,
    pub level: f32,
    generation: u64,
    task: Option<Arc<AudioTask>>,
    tx: Sender<(u64, Event)>,
    rx: Receiver<(u64, Event)>,
}

impl Default for Voice {
    fn default() -> Self {
        let (tx, rx) = mpsc::channel();
        Self {
            phase: Phase::Idle,
            level: 0.,
            generation: 0,
            task: None,
            tx,
            rx,
        }
    }
}

impl Drop for Voice {
    fn drop(&mut self) {
        self.cancel();
    }
}

impl Voice {
    pub fn cancel(&mut self) {
        if let Some(task) = self.task.take() {
            task.stop(true);
        }
        self.generation += 1;
        self.phase = Phase::Idle;
        self.level = 0.;
    }
    pub fn stop_recording(&mut self) {
        if self.phase == Phase::Recording {
            self.phase = Phase::Transcribing;
            if let Some(task) = &self.task {
                task.stop(false);
            }
        }
    }
    fn start(
        &mut self,
        phase: Phase,
        work: impl FnOnce(Arc<AudioTask>, &dyn Fn(Event)) -> Result<()> + Send + 'static,
    ) {
        self.cancel();
        self.phase = phase;
        let generation = self.generation;
        let task = Arc::new(AudioTask::default());
        self.task = Some(task.clone());
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let emit = |e| {
                let _ = tx.send((generation, e));
            };
            let result = work(task.clone(), &emit);
            if !task.cancelled() {
                if let Err(error) = result {
                    emit(Event::Error(error.to_string()));
                }
                emit(Event::Finished);
            }
            task.stop(true);
            task.reap();
        });
    }
    pub fn record(&mut self, api: Api, settings: Settings) {
        self.start(Phase::Recording, move |task, emit| {
            let limit = recording_sample_limit(&settings);
            if limit < 8000 { bail!("Calliope's recording limit is too small for dictation"); }
            #[cfg(not(any(target_os="android", target_os="ios")))]
            let mut pcm={
            let mut command = Command::new("pw-cat");
            // Device selection is local. Lab cannot start or redirect capture.
            if let Ok(target) = std::env::var("CALLIOPE_CANVAS_AUDIO_INPUT") {
                command.args(["--target", &target]);
                if std::env::var("CALLIOPE_CANVAS_AUDIO_MONITOR").as_deref() == Ok("1") {
                    command.args(["--properties", "{ stream.capture.sink = true }"]);
                }
            }
            let mut child = command.args([
                "--record", "--raw", "--rate", "16000", "--channels", "1", "--format", "s16",
                "--sample-count", &limit.to_string(), "--media-role", "Communication", "-",
            ]).stdout(Stdio::piped()).stderr(Stdio::null()).spawn()
                .context("Cannot open the microphone. Install PipeWire's pw-cat and check your audio devices.")?;
            let mut stream = child.stdout.take().context("Microphone pipe unavailable")?;
            task.install(child)?;
            let mut pcm = Vec::new();
            let mut block = [0_u8; 3200];
            loop {
                let n = stream.read(&mut block)?;
                if n == 0 { break; }
                if pcm.len() + n > limit * 2 { break; }
                pcm.extend_from_slice(&block[..n]);
                emit(Event::Level(pcm_level(&block[..n])));
            }
            task.reap();
                pcm
            };
            #[cfg(any(target_os="android", target_os="ios"))]
            let mut pcm={
                crate::host::audio_start_recording(task.id).map_err(anyhow::Error::msg)?;
                let mut pcm=Vec::new();
                while task.stop.load(Ordering::SeqCst)==0 && pcm.len()<limit*2 {
                    let bytes=crate::host::audio_read(task.id).map_err(anyhow::Error::msg)?;
                    if bytes.is_empty(){break;}
                    let n=bytes.len().min(limit*2-pcm.len());
                    pcm.extend_from_slice(&bytes[..n]);emit(Event::Level(pcm_level(&bytes[..n])));
                }
                crate::host::audio_stop(task.id,false);
                pcm
            };
            if task.cancelled() { return Ok(()); }
            pcm.truncate(pcm.len() & !1);
            if pcm.len() < 1600 { bail!("The microphone returned too little audio. Check the default input in your sound settings."); }
            if pcm_level(&pcm) < 0.00005 { bail!("The recording was silent. Check that your microphone is enabled."); }
            emit(Event::Phase(Phase::Transcribing));
            let seconds = pcm.len() as f64 / 32000.;
            let text = api.transcribe(wav(&pcm, 16000), seconds)?;
            if !task.cancelled() { emit(Event::Transcript(text)); }
            Ok(())
        });
    }
    /// Private development harness: exercises the real STT endpoint with converted
    /// fixture PCM, without pretending that Simulator has a physical microphone.
    #[cfg(all(debug_assertions, target_os = "ios"))]
    pub fn transcribe_fixture(&mut self, api: Api, pcm: Vec<u8>) {
        self.start(Phase::Transcribing, move |task, emit| {
            let text = api.transcribe(wav(&pcm,16000),pcm.len() as f64/32000.)?;
            if !task.cancelled() { emit(Event::Transcript(text)); }
            Ok(())
        });
    }
    pub fn speak(
        &mut self,
        api: Api,
        session: String,
        turn: String,
        settings: Settings,
        automatic: bool,
    ) {
        self.start(Phase::Preparing, move |task, emit| {
            let render = Api::json_response(
                api.request(reqwest::Method::POST, "/api/calliope/voice/renders")?
                    .timeout(Duration::from_secs(settings.prepare_seconds + 10))
                    .json(
                        &json!({"session_id":session,"turn_id":turn,"mode":settings.mode,
                    "personality":settings.personality}),
                    )
                    .send()?,
            )?;
            if task.cancelled() {
                return Ok(());
            }
            let mut url = string(&render, "audio_url");
            if url.is_empty() {
                bail!("Calliope did not provide audio for this reply");
            }
            if automatic {
                url.push_str(if url.contains('?') {
                    "&auto=1"
                } else {
                    "?auto=1"
                });
            }
            emit(Event::Script(string(&render["render"], "script")));
            let response = api
                .request(reqwest::Method::GET, &url)?
                .timeout(Duration::from_secs(600))
                .send()?;
            if !response.status().is_success() {
                Api::json_response(response)?;
                bail!("Calliope rejected the voice stream");
            }
            let header = |name| response.headers().get(name).and_then(|v| v.to_str().ok());
            if header("x-calliope-audio-protocol") != Some("timed-pcm-ndjson-v1")
                || header("x-audio-format") != Some("s16le")
            {
                bail!("Unsupported Calliope audio protocol");
            }
            let rate: u32 = header("x-audio-sample-rate")
                .context("Missing audio sample rate")?
                .parse()?;
            if !(8000..=96000).contains(&rate) {
                bail!("Unsupported audio sample rate: {rate}");
            }
            if task.cancelled() {
                return Ok(());
            }
            #[cfg(not(any(target_os="android", target_os="ios")))]
            let mut sink={
            let mut command = Command::new("pw-cat");
            if let Ok(target) = std::env::var("CALLIOPE_CANVAS_AUDIO_OUTPUT") {
                command.args(["--target", &target]);
            }
            let mut child = command
                .args([
                    "--playback",
                    "--raw",
                    "--rate",
                    &rate.to_string(),
                    "--channels",
                    "1",
                    "--format",
                    "s16",
                    "--media-role",
                    "Communication",
                    "-",
                ])
                .stdin(Stdio::piped())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .context(
                    "Cannot play audio. Install PipeWire's pw-cat and check your output device.",
                )?;
            let sink = child.stdin.take().context("Speaker pipe unavailable")?;
            task.install(child)?;
                sink
            };
            #[cfg(any(target_os="android", target_os="ios"))]
            let mut sink={
                crate::host::audio_start_playback(task.id,rate).map_err(anyhow::Error::msg)?;
                crate::host::AudioSink(task.id)
            };
            let mut count = 0;
            // Bound both the individual JSON frame and the total synthesized audio.
            let mut reader = BufReader::new(response);
            loop {
                let mut line = String::new();
                let n = reader
                    .by_ref()
                    .take(4 * 1024 * 1024 + 1)
                    .read_line(&mut line)?;
                if n == 0 {
                    break;
                }
                if task.cancelled() {
                    return Ok(());
                }
                if n > 4 * 1024 * 1024 {
                    bail!("Voice frame exceeds the size limit");
                }
                if let Some(bytes) = audio_frame(&line)? {
                    if count == 0 {
                        emit(Event::Phase(Phase::Playing));
                    }
                    count += bytes.len();
                    if count > 64 * 1024 * 1024 {
                        bail!("Voice response exceeds the size limit");
                    }
                    sink.write_all(&bytes)?;
                }
            }
            drop(sink);
            if count == 0 {
                bail!("Calliope returned no playable audio");
            }
            // Keep the child available to Stop while PipeWire drains buffered PCM.
            #[cfg(not(any(target_os="android", target_os="ios")))]
            loop {
                if task.cancelled() {
                    return Ok(());
                }
                let status = task
                    .child
                    .lock()
                    .unwrap()
                    .as_mut()
                    .context("Audio device closed")?
                    .try_wait()?;
                if let Some(status) = status {
                    if !status.success() {
                        bail!("Audio output stopped unexpectedly");
                    }
                    break;
                }
                std::thread::sleep(Duration::from_millis(40));
            }
            #[cfg(any(target_os="android", target_os="ios"))]
            crate::host::audio_finish_playback(task.id).map_err(anyhow::Error::msg)?;
            emit(Event::Played(count as f64 / (rate as f64 * 2.)));
            Ok(())
        });
    }
    pub fn poll(&mut self) -> Vec<Event> {
        let mut events = Vec::new();
        while let Ok((generation, event)) = self.rx.try_recv() {
            if generation != self.generation {
                continue;
            }
            match event {
                Event::Phase(phase) => self.phase = phase,
                Event::Level(level) => self.level = level,
                Event::Finished => {
                    self.phase = Phase::Idle;
                    self.level = 0.;
                    self.task = None;
                }
                _ => {}
            }
            events.push(event);
        }
        events
    }
}

pub fn recording_sample_limit(settings: &Settings) -> usize {
    (settings.max_audio_bytes.saturating_sub(44) / 2).min(settings.max_audio_seconds * 16000)
}

pub fn wav(pcm: &[u8], rate: u32) -> Vec<u8> {
    let mut out = Vec::with_capacity(pcm.len() + 44);
    out.extend_from_slice(b"RIFF");
    out.extend_from_slice(&(pcm.len() as u32 + 36).to_le_bytes());
    out.extend_from_slice(b"WAVEfmt ");
    out.extend_from_slice(&16_u32.to_le_bytes());
    out.extend_from_slice(&1_u16.to_le_bytes());
    out.extend_from_slice(&1_u16.to_le_bytes());
    out.extend_from_slice(&rate.to_le_bytes());
    out.extend_from_slice(&(rate * 2).to_le_bytes());
    out.extend_from_slice(&2_u16.to_le_bytes());
    out.extend_from_slice(&16_u16.to_le_bytes());
    out.extend_from_slice(b"data");
    out.extend_from_slice(&(pcm.len() as u32).to_le_bytes());
    out.extend_from_slice(pcm);
    out
}

fn pcm_level(bytes: &[u8]) -> f32 {
    let (sum, count) = bytes
        .as_chunks::<2>()
        .0
        .iter()
        .fold((0_f64, 0), |(sum, n), s| {
            let sample = i16::from_le_bytes([s[0], s[1]]) as f64 / 32768.;
            (sum + sample * sample, n + 1)
        });
    (sum / (count.max(1) as f64)).sqrt() as f32
}

pub fn audio_frame(line: &str) -> Result<Option<Vec<u8>>> {
    if line.trim().is_empty() {
        return Ok(None);
    }
    let frame: Value = serde_json::from_str(line).context("Malformed Calliope audio frame")?;
    match frame["type"].as_str() {
        Some("error") => bail!("{}", string(&frame, "message")),
        Some("audio") => {
            let bytes = STANDARD.decode(string(&frame, "audio_base64"))?;
            if bytes.len() % 2 != 0 {
                bail!("Incomplete PCM sample in voice frame");
            }
            Ok(if bytes.is_empty() { None } else { Some(bytes) })
        }
        _ => Ok(None),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn recording_respects_duration_and_upload_limit() {
        let mut settings = Settings::default();
        settings.max_audio_bytes = 32044;
        assert_eq!(recording_sample_limit(&settings), 16000);
        let bytes = wav(&[0; 32000], 16000);
        assert_eq!(bytes.len(), settings.max_audio_bytes);
        assert_eq!(&bytes[..4], b"RIFF");
        assert_eq!(u32::from_le_bytes(bytes[24..28].try_into().unwrap()), 16000);
    }
    #[test]
    fn audio_errors_and_truncated_samples_are_not_playable() {
        assert!(audio_frame(r#"{"type":"error","message":"failed"}"#).is_err());
        assert!(audio_frame(r#"{"type":"audio","audio_base64":"AA=="}"#).is_err());
        assert_eq!(
            audio_frame(r#"{"type":"audio","audio_base64":"AAD/fw=="}"#)
                .unwrap()
                .unwrap(),
            vec![0, 0, 255, 127]
        );
    }
    #[test]
    fn cancelled_generations_cannot_insert_late_transcripts() {
        let mut voice = Voice::default();
        let old = voice.generation;
        voice.cancel();
        voice
            .tx
            .send((old, Event::Transcript("late".into())))
            .unwrap();
        assert!(voice.poll().is_empty());
        assert_eq!(voice.phase, Phase::Idle);
    }
}

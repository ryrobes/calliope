//! UIKit owns the surface and Keychain; the app/runtime stays shared with Android.
use gpui::Application;
use serde_json::{Value, json};
use std::{
    cell::RefCell,
    ffi::{CStr, CString, c_char},
    rc::Rc,
    sync::atomic::{AtomicBool, Ordering},
};

unsafe extern "C" {
    fn calliope_ios_request(request: *const c_char) -> *mut c_char;
    fn calliope_ios_free(reply: *mut c_char);
    fn calliope_ios_log(message: *const c_char);
}

fn request(value: Value) -> Result<Value, String> {
    let input = CString::new(value.to_string()).map_err(|e| e.to_string())?;
    // The Swift host returns a newly allocated UTF-8 JSON string, freed here.
    let value = unsafe {
        let reply = calliope_ios_request(input.as_ptr());
        if reply.is_null() {
            return Err("iOS host returned no reply".into());
        }
        let bytes = CStr::from_ptr(reply).to_bytes().to_vec();
        calliope_ios_free(reply);
        serde_json::from_slice::<Value>(&bytes).map_err(|e| e.to_string())?
    };
    if let Some(error) = value["error"].as_str() {
        return Err(error.into());
    }
    Ok(value)
}

pub fn load_connection() -> Result<String, String> {
    request(json!({"op":"load_connection"}))?["value"]
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| "Invalid Keychain response".into())
}
pub fn save_connection(value: &str) -> Result<(), String> {
    request(json!({"op":"save_connection","value":value})).map(|_| ())
}
pub fn open_url(url: &str) -> Result<(), String> {
    let parsed = reqwest::Url::parse(url).map_err(|e| e.to_string())?;
    if !matches!(parsed.scheme(), "http" | "https")
        || !parsed.username().is_empty()
        || parsed.password().is_some()
    {
        return Err("Only HTTP(S) browser links are supported".into());
    }
    request(json!({"op":"open_url","url":url})).map(|_| ())
}
pub fn input_kind(kind: &str) {
    let _ = request(json!({"op":"input_kind","kind":kind}));
}
pub fn take_link() -> Option<String> {
    request(json!({"op":"take_link"})).ok()?.get("value")?.as_str().filter(|v|!v.is_empty()).map(str::to_owned)
}
// iOS owns the Home gesture; app-level Back remains in the shared navigation UI.
pub fn background() {}
pub fn take_back() -> bool {
    false
}
static FOREGROUND: AtomicBool = AtomicBool::new(false);
pub fn is_foreground() -> bool {
    FOREGROUND.load(Ordering::Relaxed)
}

thread_local! {
    static APPLICATION: RefCell<Option<gpui::ApplicationHandle>> = const { RefCell::new(None) };
}
struct Logger;
impl log::Log for Logger {
    fn enabled(&self, metadata: &log::Metadata) -> bool {
        metadata.level() <= log::Level::Info
    }
    fn log(&self, record: &log::Record) {
        if self.enabled(record.metadata()) {
            if let Ok(message) = CString::new(format!(
                "[{}] {}: {}",
                record.level(),
                record.target(),
                record.args()
            )) {
                unsafe {
                    calliope_ios_log(message.as_ptr());
                }
            }
        }
    }
    fn flush(&self) {}
}

/// Called once on UIKit's main thread with the private Application Support path.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn calliope_ios_start(files: *const c_char) {
    let _ = log::set_logger(&Logger).map(|()| log::set_max_level(log::LevelFilter::Info));
    std::panic::set_hook(Box::new(|info| log::error!("Calliope panic: {info}")));
    assert!(!files.is_null());
    let path = unsafe { CStr::from_ptr(files) }
        .to_str()
        .expect("UTF-8 app path");
    // Simulator container paths exceed sockaddr_un's 104-byte limit. Bind the
    // private inspector relative to this fixed, app-owned working directory.
    std::env::set_current_dir(path).expect("Private mobile working directory");
    assert!(
        crate::model::FILES.set(path.into()).is_ok(),
        "Mobile host starts once"
    );
    gpui_mobile::ios::ffi::gpui_ios_initialize();
    gpui_mobile::ios::ffi::gpui_ios_set_embedded();
    let app = Application::with_platform(Rc::new(gpui_mobile::ios::IosPlatform::new()))
        .with_assets(gpui_kit::assets::AllAssets)
        .run_embedded(crate::launch);
    APPLICATION.with(|slot| *slot.borrow_mut() = Some(app));
    gpui_mobile::ios::ffi::gpui_ios_did_finish_launching(std::ptr::null_mut());
}

#[unsafe(no_mangle)]
pub extern "C" fn calliope_ios_active(active: bool) {
    FOREGROUND.store(active, Ordering::Relaxed);
    if active {
        gpui_mobile::ios::ffi::gpui_ios_did_become_active(std::ptr::null_mut());
    } else {
        gpui_mobile::ios::ffi::gpui_ios_will_resign_active(std::ptr::null_mut());
    }
}

fn audio_command(mut value: Value) -> Result<Value, String> {
    value["action"] = value["op"].clone();
    value["op"] = json!("audio");
    request(value)
}
pub fn audio_status() -> serde_json::Value {
    audio_command(serde_json::json!({"op":"status","id":0}))
        .unwrap_or_else(|e| serde_json::json!({"error":e}))
}
pub fn audio_request_permission() -> Result<(), String> {
    audio_command(serde_json::json!({"op":"permission","id":0})).map(|_| ())
}
pub fn audio_start_recording(id: i64) -> Result<(), String> {
    audio_command(serde_json::json!({"op":"record","id":id})).map(|_| ())
}
pub fn audio_read(id: i64) -> Result<Vec<u8>, String> {
    use base64::Engine;
    let v = audio_command(serde_json::json!({"op":"read","id":id}))?;
    base64::engine::general_purpose::STANDARD
        .decode(v["data"].as_str().unwrap_or(""))
        .map_err(|e| e.to_string())
}
pub fn audio_stop(id: i64, _discard: bool) {
    let _ = audio_command(serde_json::json!({"op":"stop","id":id}));
}
pub fn audio_start_playback(id: i64, rate: u32) -> Result<(), String> {
    audio_command(serde_json::json!({"op":"play","id":id,"rate":rate})).map(|_| ())
}
pub fn audio_finish_playback(id: i64) -> Result<(), String> {
    audio_command(serde_json::json!({"op":"finish","id":id})).map(|_| ())
}
pub struct AudioSink(pub i64);
impl std::io::Write for AudioSink {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        use base64::Engine;
        for chunk in bytes.chunks(32768) {
            audio_command(serde_json::json!({"op":"write","id":self.0,"data":base64::engine::general_purpose::STANDARD.encode(chunk)})).map_err(std::io::Error::other)?;
        }
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

pub fn choose_attachment() -> anyhow::Result<Option<(std::path::PathBuf, String, String)>> {
    pick(None)
}
pub fn capture_photo(
    path: &std::path::Path,
) -> anyhow::Result<Option<(std::path::PathBuf, String, String)>> {
    pick(Some(path))
}
fn pick(
    path: Option<&std::path::Path>,
) -> anyhow::Result<Option<(std::path::PathBuf, String, String)>> {
    let value = request(json!({"op":"pick","camera":path})).map_err(anyhow::Error::msg)?;
    Ok(value["path"].as_str().map(|p| {
        (
            p.into(),
            value["name"].as_str().unwrap_or("attachment").into(),
            value["mime"]
                .as_str()
                .unwrap_or("application/octet-stream")
                .into(),
        )
    }))
}
pub fn open_attachment(path: &str, mime: &str) -> anyhow::Result<()> {
    request(json!({"op":"preview","path":path,"mime":mime}))
        .map_err(anyhow::Error::msg)
        .map(|_| ())
}

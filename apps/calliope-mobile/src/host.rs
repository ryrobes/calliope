//! Thin Android SurfaceView host. Platform mechanics live in gpui-mobile.
use gpui_mobile::android::{host, jni as platform_jni};
use jni::{
    EnvUnowned,
    errors::LogErrorAndDefault,
    objects::{JFloatArray, JIntArray, JObject, JString},
};
use ndk::native_window::NativeWindow;
use std::sync::atomic::{AtomicBool, Ordering};

static FOREGROUND: AtomicBool = AtomicBool::new(false);
pub fn is_foreground() -> bool { FOREGROUND.load(Ordering::Relaxed) }

pub fn input_kind(kind: &str) {
    let _ = platform_jni::with_env(|env| {
        let activity=platform_jni::activity(env)?;
        let kind=env.new_string(kind).map_err(|e|e.to_string())?;
        env.call_method(&activity, jni::jni_str!("gpuiSetInputKind"),
            jni::jni_sig!("(Ljava/lang/String;)V"), &[jni::objects::JValue::Object(&kind)])
            .map_err(|e|e.to_string())?;
        Ok(())
    });
}

static BACK: AtomicBool = AtomicBool::new(false);
pub fn take_back() -> bool {
    BACK.swap(false, Ordering::Relaxed)
}

pub fn load_connection() -> Result<String, String> {
    platform_jni::with_env(|env| {
        let activity = platform_jni::activity(env)?;
        let result = env.call_method(
            &activity,
            jni::jni_str!("loadConnection"),
            jni::jni_sig!("()Ljava/lang/String;"),
            &[],
        );
        match result {
            Ok(value) => Ok(platform_jni::get_string(
                env,
                &value.l().map_err(|e| e.to_string())?,
            )),
            Err(_) => {
                env.exception_clear();
                Err("Could not unlock the saved connection. Sign in again.".into())
            }
        }
    })
}
pub fn save_connection(value: &str) -> Result<(), String> {
    platform_jni::with_env(|env| {
        let activity = platform_jni::activity(env)?;
        let value = env.new_string(value).map_err(|e| e.to_string())?;
        if env
            .call_method(
                &activity,
                jni::jni_str!("saveConnection"),
                jni::jni_sig!("(Ljava/lang/String;)V"),
                &[jni::objects::JValue::Object(&value)],
            )
            .is_err()
        {
            env.exception_clear();
            return Err("Could not save the connection in Android Keystore".into());
        }
        Ok(())
    })
}

pub fn trusted_certificates() -> Result<String, String> {
    platform_jni::with_env(|env| {
        let activity = platform_jni::activity(env)?;
        match env.call_method(
            &activity,
            jni::jni_str!("trustedCertificates"),
            jni::jni_sig!("()Ljava/lang/String;"),
            &[],
        ) {
            Ok(value) => Ok(platform_jni::get_string(
                env,
                &value.l().map_err(|e| e.to_string())?,
            )),
            Err(_) => {
                env.exception_clear();
                Err("Could not read Android's trusted certificates".into())
            }
        }
    })
}

pub fn open_url(url: &str) -> Result<(), String> {
    let parsed = reqwest::Url::parse(url).map_err(|e| e.to_string())?;
    if !matches!(parsed.scheme(), "http" | "https")
        || !parsed.username().is_empty()
        || parsed.password().is_some()
    {
        return Err("Only HTTP(S) browser links are supported".into());
    }
    platform_jni::with_env(|env| {
        let activity = platform_jni::activity(env)?;
        let url = env.new_string(url).map_err(|e| e.to_string())?;
        env.call_method(
            &activity,
            jni::jni_str!("openBrowser"),
            jni::jni_sig!("(Ljava/lang/String;)V"),
            &[jni::objects::JValue::Object(&url)],
        )
        .map_err(|e| e.to_string())?;
        Ok(())
    })
}

pub fn take_link() -> Option<String> {
    platform_jni::with_env(|env| {
        let activity=platform_jni::activity(env)?;
        let value=env.call_method(&activity,jni::jni_str!("takeLink"),jni::jni_sig!("()Ljava/lang/String;"),&[])
            .map_err(|e|e.to_string())?.l().map_err(|e|e.to_string())?;
        Ok(if value.is_null(){None}else{Some(platform_jni::get_string(env,&value))})
    }).ok().flatten()
}

pub fn background() {
    let _ = platform_jni::with_env(|env| {
        let activity = platform_jni::activity(env)?;
        env.call_method(
            &activity,
            jni::jni_str!("backgroundApp"),
            jni::jni_sig!("()V"),
            &[],
        )
        .map_err(|e| e.to_string())?;
        Ok(())
    });
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_ai_rvbbit_calliope_mobile_CalliopeActivity_back<'a>(
    _: EnvUnowned<'a>,
    _: JObject<'a>,
) {
    BACK.store(true, Ordering::Relaxed);
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_ai_rvbbit_calliope_mobile_CalliopeActivity_create<'a>(
    mut env: EnvUnowned<'a>,
    _: JObject<'a>,
    activity: JObject<'a>,
    files: JString<'a>,
) {
    android_logger::init_once(
        android_logger::Config::default()
            .with_max_level(log::LevelFilter::Info)
            .with_tag("CalliopeMobile"),
    );
    platform_jni::install_panic_hook();
    env.with_env(|env| -> jni::errors::Result<()> {
        let _ = crate::model::FILES.set(files.try_to_string(env)?.into());
        if let Err(error) = platform_jni::set_host_activity(env, &activity) {
            log::error!("Android host: {error}");
        }
        Ok(())
    })
    .resolve::<LogErrorAndDefault>();
    host::start_with_assets(gpui_kit::assets::AllAssets, crate::launch);
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_ai_rvbbit_calliope_mobile_CalliopeActivity_surface<'a>(
    env: EnvUnowned<'a>,
    _: JObject<'a>,
    surface: JObject<'a>,
    scale: f32,
    destroyed: bool,
) {
    // Both references are valid for this JNI invocation; NativeWindow retains the surface.
    let window = unsafe { NativeWindow::from_surface(env.as_raw() as _, surface.as_raw() as _) };
    if let Some(window) = window {
        if destroyed {
            host::surface_destroyed_for(&window);
        } else {
            log::info!(
                "Surface {}x{} scale={scale}",
                window.width(),
                window.height()
            );
            host::surface_created(window, scale);
        }
    }
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_ai_rvbbit_calliope_mobile_CalliopeActivity_lifecycle<'a>(
    _: EnvUnowned<'a>,
    _: JObject<'a>,
    event: i32,
) {
    log::info!("Lifecycle {event}");
    if event == 0 || event == 1 { FOREGROUND.store(event == 1, Ordering::Relaxed); }
    match event {
        0 => host::paused(),
        1 => host::resumed(),
        _ => host::configuration_changed(),
    }
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_ai_rvbbit_calliope_mobile_CalliopeActivity_motion<'a>(
    mut env: EnvUnowned<'a>,
    _: JObject<'a>,
    action: i32,
    index: i32,
    ids: JIntArray<'a>,
    xs: JFloatArray<'a>,
    ys: JFloatArray<'a>,
) -> bool {
    env.with_env(|env| -> jni::errors::Result<bool> {
        let len = ids.len(env)?;
        let mut ids_buf = vec![0; len];
        let mut xs_buf = vec![0.; len];
        let mut ys_buf = vec![0.; len];
        ids.get_region(env, 0, &mut ids_buf)?;
        xs.get_region(env, 0, &mut xs_buf)?;
        ys.get_region(env, 0, &mut ys_buf)?;
        let pointers = (0..len)
            .map(|i| host::Pointer {
                id: ids_buf[i],
                x: xs_buf[i],
                y: ys_buf[i],
            })
            .collect::<Vec<_>>();
        Ok(host::motion_event(
            action as u32,
            index.max(0) as usize,
            &pointers,
        ))
    })
    .resolve::<LogErrorAndDefault>()
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_ai_rvbbit_calliope_mobile_CalliopeActivity_key<'a>(
    _: EnvUnowned<'a>,
    _: JObject<'a>,
    code: i32,
    action: i32,
    meta: i32,
) {
    host::key(code, action, meta);
}

#[unsafe(no_mangle)]
pub extern "system" fn Java_dev_gpui_mobile_GpuiInputActivity_mobileIme<'a>(
    mut env: EnvUnowned<'a>,
    _: JObject<'a>,
    session: i64,
    kind: i32,
    text: JString<'a>,
    start: i32,
    end: i32,
) {
    env.with_env(|env| -> jni::errors::Result<()> {
        let text = if text.is_null() {
            String::new()
        } else {
            text.try_to_string(env)?
        };
        host::ime_event(
            session as u64,
            kind,
            text,
            start.max(0) as usize,
            end.max(0) as usize,
        );
        Ok(())
    })
    .resolve::<LogErrorAndDefault>();
}

static PICKER: std::sync::Mutex<Option<std::sync::mpsc::Sender<String>>> =
    std::sync::Mutex::new(None);
pub fn choose_attachment() -> anyhow::Result<Option<(std::path::PathBuf, String, String)>> {
    pick(None)
}
pub fn capture_photo(
    path: &std::path::Path,
) -> anyhow::Result<Option<(std::path::PathBuf, String, String)>> {
    pick(Some(path))
}
fn pick(
    camera: Option<&std::path::Path>,
) -> anyhow::Result<Option<(std::path::PathBuf, String, String)>> {
    let (tx, rx) = std::sync::mpsc::channel();
    {
        let mut picker = PICKER.lock().unwrap();
        anyhow::ensure!(picker.is_none(), "File picker is already open");
        *picker = Some(tx);
    }
    let start = platform_jni::with_env(|env| {
        let activity = platform_jni::activity(env)?;
        if let Some(path) = camera {
            let path = env
                .new_string(path.to_string_lossy())
                .map_err(|e| e.to_string())?;
            env.call_method(
                &activity,
                jni::jni_str!("capturePhoto"),
                jni::jni_sig!("(Ljava/lang/String;)V"),
                &[jni::objects::JValue::Object(&path)],
            )
            .map_err(|e| e.to_string())?;
        } else {
            env.call_method(
                &activity,
                jni::jni_str!("chooseAttachment"),
                jni::jni_sig!("()V"),
                &[],
            )
            .map_err(|e| e.to_string())?;
        }
        Ok(())
    });
    if let Err(e) = start {
        PICKER.lock().unwrap().take();
        return Err(anyhow::anyhow!(e));
    }
    let value: serde_json::Value = serde_json::from_str(&rx.recv()?)?;
    if let Some(error) = value["error"].as_str() {
        anyhow::bail!(error.to_owned());
    }
    Ok(value["path"].as_str().map(|p| {
        (
            p.into(),
            value["name"].as_str().unwrap_or("attachment").to_owned(),
            value["mime"]
                .as_str()
                .unwrap_or("application/octet-stream")
                .to_owned(),
        )
    }))
}
pub fn open_attachment(path: &str, mime: &str) -> anyhow::Result<()> {
    platform_jni::with_env(|env| {
        let activity = platform_jni::activity(env)?;
        let path = env.new_string(path).map_err(|e| e.to_string())?;
        let mime = env.new_string(mime).map_err(|e| e.to_string())?;
        env.call_method(
            &activity,
            jni::jni_str!("openAttachment"),
            jni::jni_sig!("(Ljava/lang/String;Ljava/lang/String;)V"),
            &[
                jni::objects::JValue::Object(&path),
                jni::objects::JValue::Object(&mime),
            ],
        )
        .map_err(|e| e.to_string())?;
        Ok(())
    })
    .map_err(anyhow::Error::msg)
}
#[unsafe(no_mangle)]
pub extern "system" fn Java_ai_rvbbit_calliope_mobile_CalliopeActivity_attachmentPicked<'a>(
    mut env: EnvUnowned<'a>,
    _: JObject<'a>,
    value: JString<'a>,
) {
    env.with_env(|env| -> jni::errors::Result<()> {
        let value = value.try_to_string(env)?;
        if let Some(tx) = PICKER.lock().unwrap().take() {
            let _ = tx.send(value);
        }
        Ok(())
    })
    .resolve::<LogErrorAndDefault>();
}

// A session id makes cancellation from an old worker harmless to newer audio.
fn audio_command(value: serde_json::Value)->Result<serde_json::Value,String>{
    platform_jni::with_env(|env| {
        let activity=platform_jni::activity(env)?;
        let request=env.new_string(value.to_string()).map_err(|e|e.to_string())?;
        let reply=env.call_method(&activity,jni::jni_str!("audioCommand"),jni::jni_sig!("(Ljava/lang/String;)Ljava/lang/String;"),&[jni::objects::JValue::Object(&request)]).map_err(|e|e.to_string())?.l().map_err(|e|e.to_string())?;
        let value:serde_json::Value=serde_json::from_str(&platform_jni::get_string(env,&reply)).map_err(|e|e.to_string())?;
        if let Some(error)=value["error"].as_str(){return Err(error.into());}Ok(value)
    })
}
pub fn audio_status()->serde_json::Value{audio_command(serde_json::json!({"op":"status","id":0})).unwrap_or_else(|e|serde_json::json!({"error":e}))}
pub fn audio_request_permission()->Result<(),String>{audio_command(serde_json::json!({"op":"permission","id":0})).map(|_|())}
pub fn audio_start_recording(id:i64)->Result<(),String>{audio_command(serde_json::json!({"op":"record","id":id})).map(|_|())}
pub fn audio_read(id:i64)->Result<Vec<u8>,String>{
    use base64::Engine;
    let v=audio_command(serde_json::json!({"op":"read","id":id}))?;
    base64::engine::general_purpose::STANDARD.decode(v["data"].as_str().unwrap_or("")).map_err(|e|e.to_string())
}
pub fn audio_stop(id:i64,_discard:bool){let _=audio_command(serde_json::json!({"op":"stop","id":id}));}
pub fn audio_start_playback(id:i64,rate:u32)->Result<(),String>{audio_command(serde_json::json!({"op":"play","id":id,"rate":rate})).map(|_|())}
pub fn audio_finish_playback(id:i64)->Result<(),String>{audio_command(serde_json::json!({"op":"finish","id":id})).map(|_|())}
pub struct AudioSink(pub i64);
impl std::io::Write for AudioSink {
    fn write(&mut self,bytes:&[u8])->std::io::Result<usize>{
        use base64::Engine;
        for chunk in bytes.chunks(32768){audio_command(serde_json::json!({"op":"write","id":self.0,"data":base64::engine::general_purpose::STANDARD.encode(chunk)})).map_err(std::io::Error::other)?;}
        Ok(bytes.len())
    }
    fn flush(&mut self)->std::io::Result<()>{Ok(())}
}

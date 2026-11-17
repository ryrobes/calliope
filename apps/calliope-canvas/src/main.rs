mod app_context;
mod native_link;
mod app_pins;
mod app_assistant;
// Phase-0 reply outputs; the phone renders them, the canvas adopts them with the card host.
#[allow(dead_code)]
mod turn_outputs;
mod app_stream;
mod api;
mod app_drafts;
mod app_workspace;
mod app_connection;
mod board;
mod brand;
mod business;
mod business_drafts;
mod business_filters;
mod business_outbox;
mod business_probe;
mod business_reactive;
mod charts;
mod deferred_save;
mod grid;
mod live;
mod mailbox;
mod model;
mod meaning;
mod published_apps;
mod server_charts;
#[allow(dead_code)] // Desktop draws charts; answering cards is phone-only for now.
mod stage_cards;
mod theme;
mod ui;
mod voice;

use gpui::*;
use gpui_component::Root;

fn main() -> anyhow::Result<()> {
    if std::env::args().any(|s| s == "--check") {
        let api = api::Api::new(api::Connection::load())?;
        let sessions = api.sessions()?;
        let mut objects = 0;
        for s in sessions.iter().filter(|s| s.surface_count > 0).take(3) {
            objects += api.thread(&s.id)?.surfaces.len();
        }
        println!(
            "Lab connection verified: {} conversations; {objects} objects read from 3 conversations.",
            sessions.len()
        );
        return Ok(());
    }
    Application::new().run(|cx| {
        gpui_component::init(cx);
        brand::init(cx);
        let fonts = theme::cached_fonts(&api::Connection::load().base_url);
        if let Err(error) = cx
            .text_system()
            .add_fonts(fonts.into_iter().map(std::borrow::Cow::Owned).collect())
        {
            eprintln!("Cached theme fonts: {error}");
        }
        theme::CanvasTheme::default().apply(cx);
        let bounds = Bounds::centered(None, size(px(1440.), px(1000.)), cx);
        cx.open_window(
            WindowOptions {
                window_bounds: Some(WindowBounds::Windowed(bounds)),
                titlebar: Some(TitlebarOptions {
                    title: Some("Calliope Canvas · Lab".into()),
                    ..Default::default()
                }),
                app_id: Some("ai.calliope.canvas".into()),
                window_min_size: Some(size(px(1000.), px(700.))),
                ..Default::default()
            },
            |window, cx| {
                window.set_window_title("Calliope Canvas · Lab");
                let view = cx.new(|cx| ui::CanvasApp::new(window, cx));
                cx.new(|cx| Root::new(view, window, cx))
            },
        )
        .expect("Could not open the native canvas window");
        cx.activate(true);
        cx.on_window_closed(|cx| {
            if cx.windows().is_empty() {
                cx.quit();
            }
        })
        .detach();
    });
    Ok(())
}

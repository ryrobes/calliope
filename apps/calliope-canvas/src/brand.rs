//! Shared native branding, available before sign-in and without a network.
use gpui::{prelude::*, *};
use std::{
    borrow::Cow,
    sync::{Arc, OnceLock},
};

pub fn init(cx: &App) {
    if let Err(error) = cx
        .text_system()
        .add_fonts(vec![Cow::Borrowed(include_bytes!(
            "../assets/fonts/HomemadeApple-Regular.ttf"
        ))])
    {
        eprintln!("Calliope signature font: {error}");
    }
}

pub fn avatar(size: f32) -> impl IntoElement {
    static IMAGE: OnceLock<Arc<Image>> = OnceLock::new();
    let image = IMAGE.get_or_init(|| {
        Arc::new(Image::from_bytes(
            ImageFormat::Png,
            include_bytes!("../assets/calliope-avatar.png").to_vec(),
        ))
    });
    img(image.clone())
        .size(px(size))
        .flex_shrink_0()
        .object_fit(ObjectFit::Contain)
}

pub fn wordmark(size: f32) -> Div {
    div()
        .font_family("Homemade Apple")
        .font_weight(FontWeight::NORMAL)
        .text_size(px(size))
        .line_height(relative(1.65))
        .whitespace_nowrap()
        .pl(px(2.))
        .pr(px(6.))
        .child("Calliope")
}

pub fn lockup(avatar_size: f32, type_size: f32) -> Div {
    div()
        .flex()
        .items_center()
        .gap_3()
        .child(avatar(avatar_size))
        .child(wordmark(type_size))
}

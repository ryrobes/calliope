use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::{HashMap, HashSet},
    path::PathBuf,
};

pub fn string(v: &Value, key: &str) -> String {
    v.get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

pub fn display(v: &Value) -> String {
    match v {
        Value::Null => "—".into(),
        Value::String(s) => s.clone(),
        _ => v.to_string(),
    }
}

pub fn short(s: &str, limit: usize) -> String {
    let mut chars = s.chars();
    let head: String = chars.by_ref().take(limit).collect();
    if chars.next().is_some() {
        format!("{head}…")
    } else {
        head
    }
}

#[derive(Clone, Debug, Deserialize, Default)]
pub struct Session {
    pub id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub surface_count: usize,
    #[serde(default)]
    pub channel: String,
    #[serde(default)]
    pub access_role: String,
    #[serde(default)]
    pub synopsis: Option<String>,
}

impl Session {
    pub fn writable(&self) -> bool {
        (self.channel.is_empty() || self.channel == "web")
            && matches!(self.access_role.as_str(), "owner" | "participant")
    }
}

#[derive(Clone, Debug, Deserialize)]
pub struct Surface {
    pub id: String,
    pub session_id: String,
    pub kind: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub lineage_key: Option<String>,
    #[serde(default)]
    pub artifact_version: Option<i64>,
    #[serde(default)]
    pub created_at: String,
    #[serde(default)]
    pub payload: Value,
    #[serde(default)]
    pub source: Value,
    #[serde(default)]
    pub presentation: Value,
}

impl Surface {
    pub fn useful(&self) -> bool {
        matches!(
            self.kind.as_str(),
            "query"
                | "answer"
                | "document"
                | "artifact"
                | "image"
                | "metric"
                | "chart"
                | "map"
                | "board"
                | "cube"
                | "email"
                | "input"
        ) && self.presentation.get("hidden").and_then(Value::as_bool) != Some(true)
            && self.source.get("suppressed").and_then(Value::as_bool) != Some(true)
    }

    pub fn priority(&self) -> usize {
        match self.kind.as_str() {
            "artifact" => 0,
            "metric" => 1,
            "answer" => 2,
            "board" => 3,
            "query" if self.payload["metadata_query"] != true => 4,
            "image" => 5,
            "document" => 6,
            "chart" => 7,
            _ => 10,
        }
    }

    pub fn description(&self) -> String {
        for path in [
            "/manifest/description",
            "/description",
            "/summary",
            "/markdown",
            "/text",
            "/content",
            "/body",
            "/prompt",
        ] {
            if let Some(s) = self.payload.pointer(path).and_then(Value::as_str)
                && !s.trim().is_empty()
            {
                return s.to_owned();
            }
        }
        self.source
            .get("question")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned()
    }
}

/// The API returns newest first. An exact-version pin remains exact; only the
/// browsing projection folds previous members of the same lineage.
pub fn latest(surfaces: &[Surface]) -> Vec<Surface> {
    let mut seen = HashSet::new();
    let mut result = Vec::new();
    for s in surfaces.iter().filter(|s| s.useful()) {
        let key = s
            .lineage_key
            .clone()
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| s.id.clone());
        if seen.insert(key) {
            result.push(s.clone());
        }
    }
    result
}

#[derive(Clone, Debug, Deserialize)]
pub struct Thread {
    pub session: Session,
    #[serde(default)]
    pub surfaces: Vec<Surface>,
    #[serde(default)]
    pub turns: Vec<Value>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Pin {
    pub id: String,
    pub session_id: String,
    pub surface_id: String,
    #[serde(default)]
    pub query_id: Option<String>,
    /// A cell in this exact board surface, not a copy of its result or layout.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub board_cell: Option<usize>,
    pub title: String,
    pub x: f32,
    pub y: f32,
    pub width: f32,
    pub height: f32,
    #[serde(default)]
    pub space: String,
}

impl Pin {
    /// Pointer deltas are screen pixels; placement geometry is in canvas units.
    pub fn resize_from(&mut self, original: [f32; 2], delta: [f32; 2], zoom: f32) {
        self.width = (original[0] + delta[0] / zoom).max(240.);
        self.height = (original[1] + delta[1] / zoom).max(160.);
    }

    /// Stable content identity. A Pin is only a placement; every placement of
    /// this exact source/query shares one preview and one refresh policy.
    pub fn object_key(&self) -> String {
        if let Some(cell) = self.board_cell {
            return serde_json::to_string(&(
                &self.session_id,
                &self.surface_id,
                &self.query_id,
                cell,
            ))
            .unwrap();
        }
        serde_json::to_string(&(&self.session_id, &self.surface_id, &self.query_id)).unwrap()
    }

    pub fn from_surface(s: &Surface, x: f32, y: f32, space: &str) -> Self {
        Self {
            id: uuid::Uuid::new_v4().to_string(),
            session_id: s.session_id.clone(),
            surface_id: s.id.clone(),
            query_id: None,
            board_cell: None,
            title: s.title.clone(),
            x,
            y,
            width: if s.kind == "board" { 960. } else { 352. },
            height: if s.kind == "board" { 640. } else { 264. },
            space: space.to_owned(),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Camera {
    pub pan: [f32; 2],
    pub zoom: f32,
}
impl Default for Camera {
    fn default() -> Self {
        Self {
            pan: [36., 24.],
            zoom: 1.,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Topic {
    pub session_id: String,
    pub title: String,
    pub x: f32,
    pub y: f32,
    pub pinned: bool,
    #[serde(default)]
    pub hidden: HashSet<String>,
    #[serde(default)]
    pub seen: HashSet<String>,
    #[serde(default)]
    pub unread: bool,
}
impl Topic {
    pub fn space(&self) -> String {
        topic_space(&self.session_id)
    }
}

pub fn topic_space(session: &str) -> String {
    format!("topic:{session}")
}
fn curation_key(s: &Surface) -> String {
    s.lineage_key
        .clone()
        .filter(|k| !k.is_empty())
        .unwrap_or_else(|| s.id.clone())
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct Home {
    pub version: u32,
    // Prevent a profile/instance switch from displaying another account's pins.
    pub account: String,
    pub pins: Vec<Pin>,
    pub pan: [f32; 2],
    pub zoom: f32,
    pub seeded: bool,
    pub active_space: String,
    pub spaces: Vec<String>,
    pub topics: Vec<Topic>,
    pub cameras: HashMap<String, Camera>,
    /// Object keys, never placement IDs. Opt-in for bounded automatic reads.
    pub live_objects: HashSet<String>,
    /// Board source keys: false = manual reads, true = automatic reads.
    /// Absence means show the saved receipt. Results are never persisted here.
    pub board_queries: HashMap<String, bool>,
    /// Local business cases share the canvas but are not Lab stage receipts.
    pub case_positions: HashMap<String, [f32; 2]>,
    /// Device-specific placement only. Server-owned record identities live in Postgres.
    pub record_positions: HashMap<String, [f32; 4]>,
    pub meaning_positions: HashMap<String, [f32; 4]>,
    pub meaning_topics: HashMap<String, String>,
    pub case_topics: HashMap<String, String>,
}

impl Default for Home {
    fn default() -> Self {
        Self {
            version: 2,
            account: String::new(),
            pins: vec![],
            pan: [36., 24.],
            zoom: 1.,
            seeded: false,
            active_space: "Home".into(),
            spaces: vec!["Home".into(), "Workbench".into()],
            topics: vec![],
            cameras: HashMap::new(),
            live_objects: HashSet::new(),
            board_queries: HashMap::new(),
            case_positions: HashMap::new(),
            record_positions: HashMap::new(),
            meaning_positions: HashMap::new(),
            meaning_topics: HashMap::new(),
            case_topics: HashMap::new(),
        }
    }
}

impl Home {
    pub fn load(account: &str) -> Self {
        let path = data_dir().join("home.json");
        let bytes = std::fs::read(&path).ok();
        let mut value: Self = bytes
            .as_ref()
            .and_then(|s| serde_json::from_slice(s).ok())
            .unwrap_or_default();
        if value.account != account || !matches!(value.version, 1 | 2) {
            value = Self::default();
        } else if value.version == 1 {
            // Preserve the original bytes once, before the first v2 write.
            let backup = data_dir().join("home-before-topics.json");
            if !backup.exists()
                && let Some(bytes) = &bytes
            {
                let _ = private_write(&backup, bytes);
            }
            value.version = 2;
        }
        value.account = account.into();
        value.sanitize();
        value
    }
    fn sanitize(&mut self) {
        if !self.zoom.is_finite() {
            self.zoom = 1.;
        }
        self.zoom = self.zoom.clamp(0.25, 1.6);
        if self.pan.iter().any(|n| !n.is_finite()) {
            self.pan = [36., 24.];
        }
        self.pins.retain(|p| {
            [p.x, p.y, p.width, p.height].iter().all(|n| n.is_finite())
                && p.width >= 240.
                && p.height >= 160.
        });
        self.topics.retain(|t| t.x.is_finite() && t.y.is_finite());
        self.cameras
            .retain(|_, c| c.zoom.is_finite() && c.pan.iter().all(|v| v.is_finite()));
    }
    pub fn save(&mut self) -> anyhow::Result<()> {
        self.remember_camera();
        private_write(
            &data_dir().join("home.json"),
            &serde_json::to_vec_pretty(self)?,
        )
    }
    fn remember_camera(&mut self) {
        self.cameras.insert(
            self.active_space.clone(),
            Camera {
                pan: self.pan,
                zoom: self.zoom,
            },
        );
    }
    pub fn switch_space(&mut self, space: String) {
        self.remember_camera();
        let camera = self.cameras.get(&space).cloned().unwrap_or_default();
        self.active_space = space;
        self.pan = camera.pan;
        self.zoom = camera.zoom.clamp(0.25, 1.6);
    }
    pub fn active_topic(&self) -> Option<&Topic> {
        self.topics.iter().find(|t| t.space() == self.active_space)
    }
    pub fn ensure_topic(&mut self, session: &Session, pin: bool) {
        if let Some(t) = self.topics.iter_mut().find(|t| t.session_id == session.id) {
            t.title = session.title.clone();
            t.pinned |= pin;
            return;
        }
        let n = self.topics.len();
        // New bubbles sit above existing Home objects; their geometry is intact.
        let top = self
            .pins
            .iter()
            .filter(|p| p.space == "Home")
            .map(|p| p.y)
            .fold(0., f32::min);
        self.topics.push(Topic {
            session_id: session.id.clone(),
            title: session.title.clone(),
            x: (n % 3) as f32 * 380.,
            y: top - 244. - (n / 3) as f32 * 228.,
            pinned: pin,
            hidden: HashSet::new(),
            seen: HashSet::new(),
            unread: false,
        });
        // Existing extracted pieces also belong to their source topic.
        let space = topic_space(&session.id);
        let pieces: Vec<_> = self
            .pins
            .iter()
            .filter(|p| p.session_id == session.id && p.query_id.is_some())
            .cloned()
            .collect();
        for p in pieces {
            self.place_in(&p, &space);
        }
    }
    pub fn sync_topic(&mut self, thread: &Thread) {
        let Some(index) = self
            .topics
            .iter()
            .position(|t| t.session_id == thread.session.id)
        else {
            return;
        };
        let space = self.topics[index].space();
        self.topics[index].title = thread.session.title.clone();
        // A thread can have hundreds of receipts. Start with a small working
        // selection; everything else remains available in the topic browser.
        let mut slots = if self.topics[index].seen.is_empty() {
            8usize.saturating_sub(self.pins.iter().filter(|p| p.space == space).count())
        } else {
            4
        };
        let mut items = latest(&thread.surfaces);
        items.sort_by_key(Surface::priority);
        for s in items {
            let t = &mut self.topics[index];
            if !t.seen.insert(s.id.clone()) || t.hidden.contains(&curation_key(&s)) {
                continue;
            }
            if self.active_space != space {
                t.unread = true;
            }
            if slots == 0 {
                continue;
            }
            slots -= 1;
            let p = Pin::from_surface(&s, 0., 0., &space);
            self.place_in(&p, &space);
        }
        if self.active_space == space {
            self.topics[index].unread = false;
        }
    }
    pub fn place_in(&mut self, source: &Pin, space: &str) -> String {
        let key = source.object_key();
        if let Some(p) = self
            .pins
            .iter()
            .find(|p| p.space == space && p.object_key() == key)
        {
            return p.id.clone();
        }
        let mut p = source.clone();
        p.id = uuid::Uuid::new_v4().to_string();
        p.space = space.into();
        // Find an unoccupied slot without rearranging existing objects.
        let mut n = 0;
        loop {
            p.x = (n % 3) as f32 * 480.;
            p.y = (n / 3) as f32 * 330.;
            if !self.pins.iter().filter(|q| q.space == space).any(|q| {
                p.x < q.x + q.width + 20.
                    && p.x + p.width + 20. > q.x
                    && p.y < q.y + q.height + 20.
                    && p.y + p.height + 20. > q.y
            }) {
                break;
            }
            n += 1;
        }
        let id = p.id.clone();
        self.pins.push(p);
        id
    }
    pub fn hide_placement(&mut self, id: &str, surface: Option<&Surface>) {
        if let Some(p) = self.pins.iter().find(|p| p.id == id)
            && let Some(t) = self.topics.iter_mut().find(|t| t.space() == p.space)
            && p.query_id.is_none()
            && p.board_cell.is_none()
        {
            t.hidden.insert(
                surface
                    .map(curation_key)
                    .unwrap_or_else(|| p.surface_id.clone()),
            );
        }
        self.pins.retain(|p| p.id != id);
    }
    pub fn restore_surface(&mut self, s: &Surface) -> String {
        if let Some(t) = self
            .topics
            .iter_mut()
            .find(|t| t.space() == self.active_space)
        {
            t.hidden.remove(&curation_key(s));
        }
        self.add(s)
    }

    pub fn add(&mut self, s: &Surface) -> String {
        let space = self.active_space.clone();
        self.place_in(&Pin::from_surface(s, 0., 0., &space), &space)
    }
}

pub fn data_dir() -> PathBuf {
    dirs::data_local_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("calliope-canvas")
}
pub fn config_path() -> PathBuf {
    dirs::config_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("calliope-canvas/connection.json")
}

pub fn private_write(path: &std::path::Path, bytes: &[u8]) -> anyhow::Result<()> {
    use std::{
        io::Write,
        os::unix::fs::{OpenOptionsExt, PermissionsExt},
    };
    let dir = path.parent().unwrap();
    std::fs::create_dir_all(dir)?;
    std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))?;
    let tmp = path.with_extension("pending");
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .mode(0o600)
        .open(&tmp)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    std::fs::rename(tmp, path)?;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn surface(id: &str) -> Surface {
        serde_json::from_value(serde_json::json!({"id":id,"session_id":"s","kind":"artifact","title":"Brief","lineage_key":"brief"})).unwrap()
    }
    #[test]
    fn browsing_folds_versions_but_pins_keep_exact_identity() {
        let a = surface("new");
        let b = surface("old");
        assert_eq!(latest(&[a.clone(), b.clone()]).len(), 1);
        assert_eq!(Pin::from_surface(&b, 0., 0., "Home").surface_id, "old");
    }
    #[test]
    fn duplicate_pins_are_per_space() {
        let mut h = Home::default();
        let s = surface("one");
        assert_eq!(h.add(&s), h.add(&s));
        h.active_space = "Workbench".into();
        h.add(&s);
        assert_eq!(h.pins.len(), 2);
    }
    #[test]
    fn mirrored_channels_are_read_only() {
        let mut s = Session {
            channel: "sms".into(),
            access_role: "owner".into(),
            ..Default::default()
        };
        assert!(!s.writable());
        s.channel = "web".into();
        assert!(s.writable());
        s.access_role = "viewer".into();
        assert!(!s.writable());
    }

    fn topic_thread() -> Thread {
        Thread {
            session: Session {
                id: "s".into(),
                title: "Supplier costs".into(),
                ..Default::default()
            },
            surfaces: vec![surface("one")],
            turns: vec![],
        }
    }

    #[test]
    fn legacy_layout_deserializes_without_moving_or_losing_pins() {
        let mut old = serde_json::to_value(Home::default()).unwrap();
        old["version"] = 1.into();
        old["pan"] = serde_json::json!([-91., 17.]);
        old["zoom"] = 0.7.into();
        old["pins"] =
            serde_json::json!([Pin::from_surface(&surface("one"), 127., -41., "Workbench")]);
        for field in ["topics", "cameras", "live_objects"] {
            old.as_object_mut().unwrap().remove(field);
        }
        let mut restored: Home = serde_json::from_value(old).unwrap();
        restored.sanitize();
        restored.ensure_topic(&topic_thread().session, true);
        assert_eq!(restored.pins.len(), 1);
        assert_eq!((restored.pins[0].x, restored.pins[0].y), (127., -41.));
        assert_eq!(restored.pins[0].space, "Workbench");
        assert_eq!(restored.pan, [-91., 17.]);
        assert_eq!(restored.zoom, 0.7);
    }

    #[test]
    fn hiding_one_board_cell_does_not_hide_the_board_lineage() {
        let mut h = Home::default();
        let t = topic_thread();
        h.ensure_topic(&t.session, true);
        let mut board = surface("board");
        board.kind = "board".into();
        let parent = Pin::from_surface(&board, 0., 0., &topic_space("s"));
        let mut cell = parent.clone();
        cell.id = "cell".into();
        cell.board_cell = Some(1);
        h.pins.extend([parent.clone(), cell.clone()]);
        h.hide_placement(&cell.id, Some(&board));
        assert!(h.topics[0].hidden.is_empty());
        assert_eq!(h.pins[0].id, parent.id);
    }

    #[test]
    fn resizing_uses_canvas_scale_and_preserves_other_placements() {
        let mut h = Home::default();
        let topic = Pin::from_surface(&surface("one"), 31., 42., "topic:s");
        h.pins.push(topic.clone());
        let id = h.place_in(&topic, "Home");
        let pin = h.pins.iter_mut().find(|p| p.id == id).unwrap();
        let position = (pin.x, pin.y);
        pin.resize_from([352., 264.], [200., 100.], 0.5);
        assert_eq!((pin.width, pin.height), (752., 464.));
        assert_eq!((pin.x, pin.y), position);
        let restored: Home = serde_json::from_value(serde_json::to_value(&h).unwrap()).unwrap();
        assert_eq!(restored.pins[0].width, 352.);
        assert_eq!(restored.pins[1].width, 752.);
        let pin = &mut h.pins[1];
        pin.resize_from([752., 464.], [-2000., -2000.], 1.5);
        assert_eq!((pin.width, pin.height), (240., 160.));
    }

    #[test]
    fn placements_share_content_but_keep_independent_geometry_and_visibility() {
        let mut h = Home::default();
        let t = topic_thread();
        h.ensure_topic(&t.session, true);
        let mut piece = Pin::from_surface(&t.surfaces[0], 31., 42., &topic_space("s"));
        piece.query_id = Some("spend".into());
        let key = piece.object_key();
        h.pins.push(piece.clone());
        let home_id = h.place_in(&piece, "Home");
        assert_eq!(home_id, h.place_in(&piece, "Home"));
        let placement = h.pins.iter_mut().find(|p| p.id == home_id).unwrap();
        placement.x = 900.;
        assert_eq!(placement.object_key(), key);
        assert_eq!(h.pins[0].x, 31.);
        h.live_objects.insert(key.clone());
        h.hide_placement(&piece.id, Some(&t.surfaces[0]));
        assert_eq!(h.pins.len(), 1);
        assert_eq!(h.pins[0].id, home_id);
        assert!(h.live_objects.contains(&h.pins[0].object_key()));
        let mut revised = h.pins[0].clone();
        revised.surface_id = "new-version".into();
        assert_ne!(revised.object_key(), key);
    }

    #[test]
    fn hidden_lineage_stays_hidden_after_sync_and_can_be_restored() {
        let mut h = Home::default();
        let mut t = topic_thread();
        h.ensure_topic(&t.session, true);
        h.switch_space(topic_space("s"));
        h.sync_topic(&t);
        let pin = h.pins[0].clone();
        h.place_in(&pin, "Home");
        h.hide_placement(&pin.id, Some(&t.surfaces[0]));
        h.sync_topic(&t);
        t.surfaces.insert(0, surface("revised"));
        h.sync_topic(&t);
        assert_eq!(h.pins.len(), 1); // Home placement remains the original receipt.
        assert_eq!(h.pins[0].surface_id, "one");
        h.restore_surface(&t.surfaces[0]);
        h.sync_topic(&t);
        assert_eq!(h.pins.len(), 2);
        assert!(h.topics[0].hidden.is_empty());
    }

    #[test]
    fn topic_sync_is_idempotent_and_never_promotes_objects_home() {
        let mut h = Home::default();
        let t = topic_thread();
        h.ensure_topic(&t.session, true);
        h.sync_topic(&t);
        h.sync_topic(&t);
        assert_eq!(h.pins.len(), 1);
        assert_eq!(h.pins[0].space, topic_space("s"));
        assert!(h.topics[0].unread);
        h.switch_space(topic_space("s"));
        h.sync_topic(&t);
        assert!(!h.topics[0].unread);
        h.topics[0].pinned = false;
        h.sync_topic(&t);
        assert!(!h.topics[0].pinned);
    }

    #[test]
    fn large_threads_start_with_a_small_selection_and_add_bounded_new_work() {
        let mut h = Home::default();
        let mut t = topic_thread();
        t.surfaces = (0..40)
            .map(|i| {
                let mut s = surface(&format!("receipt-{i}"));
                s.lineage_key = None;
                s
            })
            .collect();
        h.ensure_topic(&t.session, true);
        h.sync_topic(&t);
        assert_eq!(h.pins.len(), 8);
        assert_eq!(h.topics[0].seen.len(), 40);
        h.sync_topic(&t);
        assert_eq!(h.pins.len(), 8);
        for i in 40..50 {
            let mut s = surface(&format!("receipt-{i}"));
            s.lineage_key = None;
            t.surfaces.insert(0, s);
        }
        h.sync_topic(&t);
        assert_eq!(h.pins.len(), 12);
        h.switch_space(topic_space("s"));
        // An omitted receipt is still available for deliberate curation.
        h.restore_surface(&t.surfaces[5]);
        assert_eq!(h.pins.len(), 13);
    }

    #[test]
    fn each_canvas_restores_its_camera_after_navigation_and_reload() {
        let mut h = Home {
            pan: [-100., 80.],
            zoom: 0.7,
            ..Default::default()
        };
        h.switch_space(topic_space("s"));
        assert_eq!(h.pan, [36., 24.]);
        h.pan = [-540., 120.];
        h.zoom = 1.3;
        h.switch_space("Home".into());
        assert_eq!(h.pan, [-100., 80.]);
        assert_eq!(h.zoom, 0.7);
        let mut h: Home = serde_json::from_slice(&serde_json::to_vec(&h).unwrap()).unwrap();
        h.switch_space(topic_space("s"));
        assert_eq!(h.pan, [-540., 120.]);
        assert_eq!(h.zoom, 1.3);
    }
}

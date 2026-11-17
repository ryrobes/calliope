// Screen-space label placement. Cards remain the final drawing layer as an
// additional guard; a connection without a safe label stays in the inspector.
#[derive(Clone, Serialize)]
struct EdgeLabel {
    id: String,
    label: String,
    rect: [f32; 4],
}
fn overlaps(a: [f32; 4], b: [f32; 4], padding: f32) -> bool {
    a[0] < b[0] + b[2] + padding
        && a[0] + a[2] + padding > b[0]
        && a[1] < b[1] + b[3] + padding
        && a[1] + a[3] + padding > b[1]
}
fn safe_label(
    curve: [[f32; 2]; 4],
    width: f32,
    occupied: &[[f32; 4]],
    viewport: [f32; 2],
) -> Option<[f32; 4]> {
    let [a, b, c, d] = curve;
    for t in [
        0.5_f32, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8, 0.12, 0.88, 0.06, 0.94,
    ] {
        let u = 1. - t;
        let p = [
            u * u * u * a[0] + 3. * u * u * t * c[0] + 3. * u * t * t * d[0] + t * t * t * b[0],
            u * u * u * a[1] + 3. * u * u * t * c[1] + 3. * u * t * t * d[1] + t * t * t * b[1],
        ];
        let rect = [p[0] - width / 2., p[1] - 9., width, 18.];
        if rect[0] >= 6.
            && rect[1] >= 6.
            && rect[0] + width <= viewport[0] - 6.
            && rect[1] + 18. <= viewport[1] - 26.
            && !occupied.iter().any(|r| overlaps(rect, *r, 6.))
        {
            return Some(rect);
        }
    }
    None
}
impl Meaning {
    fn edge_labels(&self) -> Vec<EdgeLabel> {
        let l = self.layout();
        if l.zoom < 0.6 {
            return vec![];
        }
        let display_visible = self.display_visible();
        let mut occupied: Vec<_> = l
            .positions
            .iter()
            .filter(|(id, _)| display_visible.contains(*id))
            .map(|(_, p)| {
                [
                    l.pan[0] + p[0] * l.zoom,
                    l.pan[1] + p[1] * l.zoom,
                    300. * l.zoom,
                    170. * l.zoom,
                ]
            })
            .collect();
        let mut labels = vec![];
        let mut edges = self.display_edges();
        edges.sort_by_key(|e| Some(text(e, "id")) != self.selected_edge);
        for e in edges.iter().filter(|e| {
            (e["from"] == self.selected || e["to"] == self.selected)
                && display_visible.contains(&text(e, "from"))
                && display_visible.contains(&text(e, "to"))
        }) {
            let (Some(a), Some(b)) = (
                l.positions.get(&text(e, "from")),
                l.positions.get(&text(e, "to")),
            ) else {
                continue;
            };
            let (a, b, c, d) = edge_points(*a, *b);
            let transform = |p: [f32; 2]| [l.pan[0] + p[0] * l.zoom, l.pan[1] + p[1] * l.zoom];
            let label = text(e, "label");
            let width = label.chars().count() as f32 * 6.5 + 14.;
            if let Some(rect) = safe_label(
                [transform(a), transform(b), transform(c), transform(d)],
                width,
                &occupied,
                [
                    f32::from(self.viewport.size.width),
                    f32::from(self.viewport.size.height),
                ],
            ) {
                occupied.push(rect);
                labels.push(EdgeLabel {
                    id: text(e, "id"),
                    label,
                    rect,
                });
            }
        }
        labels
    }
}
#[cfg(test)]
mod geometry_tests {
    use super::*;
    #[::core::prelude::v1::test]
    fn labels_move_off_intervening_tiles_and_other_labels() {
        let curve = [[50., 100.], [1050., 100.], [550., 100.], [550., 100.]];
        let mut occupied = vec![[490., 30., 140., 150.]];
        let first = safe_label(curve, 110., &occupied, [1100., 300.]).unwrap();
        assert!(!overlaps(first, occupied[0], 6.));
        occupied.push(first);
        let second = safe_label(curve, 110., &occupied, [1100., 300.]).unwrap();
        assert!(occupied.iter().all(|r| !overlaps(second, *r, 6.)));
    }
    #[::core::prelude::v1::test]
    fn crowded_or_offscreen_edges_have_no_floating_label() {
        let curve = [[50., 100.], [1050., 100.], [550., 100.], [550., 100.]];
        assert!(safe_label(curve, 110., &[[0., 0., 1100., 300.]], [1100., 300.]).is_none());
        assert!(safe_label(curve, 110., &[], [20., 20.]).is_none());
    }
}

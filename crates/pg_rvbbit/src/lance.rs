//! Compatibility surface for the removed Lance backend.
//!
//! Lance was removed from the compiled extension so Arrow, Parquet, and
//! DataFusion can move independently. Keep the old SQL symbols for one
//! release so existing catalogs and automation fail with a useful message
//! instead of an undefined-function error. The KG resolver probe is the one
//! exception: it returns no candidates so `kg_resolve_node` continues into its
//! existing embedding fallback.

use pgrx::prelude::*;
use pgrx::JsonB;
use serde_json::json;

const REMOVED_MESSAGE: &str =
    "the Lance backend has been removed; use Parquet/Vortex acceleration and embedding search";

fn removed<T>(function: &str) -> T {
    pgrx::error!("rvbbit.{function}: {REMOVED_MESSAGE}")
}

#[pg_extern]
fn lance_create_demo(path: &str, n_rows: i64, dim: i32) -> i64 {
    let _ = (path, n_rows, dim);
    removed("lance_create_demo")
}

#[pg_extern]
fn lance_count(path: &str) -> i64 {
    let _ = path;
    removed("lance_count")
}

#[pg_extern]
fn lance_knn(path: &str, query: Vec<f32>, k: i32) -> JsonB {
    let _ = (path, query, k);
    removed("lance_knn")
}

#[pg_extern]
fn lance_enable_text(
    reloid: pg_sys::Oid,
    col: &str,
    lance_url: &str,
    specialist: default!(&str, "''"),
) -> JsonB {
    let _ = (reloid, col, lance_url, specialist);
    removed("lance_enable_text")
}

#[pg_extern]
fn lance_refresh_text(reloid: pg_sys::Oid, col: &str, specialist: default!(&str, "''")) -> JsonB {
    let _ = (reloid, col, specialist);
    removed("lance_refresh_text")
}

#[pg_extern]
fn kg_lance_enable(
    node_kind: &str,
    graph: default!(&str, "''"),
    specialist: default!(&str, "''"),
    lance_url: default!(&str, "''"),
) -> JsonB {
    let _ = (node_kind, graph, specialist, lance_url);
    removed("kg_lance_enable")
}

#[pg_extern]
fn kg_lance_refresh(
    node_kind: &str,
    graph: default!(&str, "''"),
    specialist: default!(&str, "''"),
) -> JsonB {
    let _ = (node_kind, graph, specialist);
    removed("kg_lance_refresh")
}

#[pg_extern]
fn kg_lance_resolve_nodes(
    node_kind: &str,
    node_label: &str,
    specialist: default!(&str, "''"),
    match_threshold: default!(f64, "0.92"),
    graph: default!(&str, "''"),
    limit_count: default!(i32, "10"),
) -> JsonB {
    let _ = (
        node_kind,
        node_label,
        specialist,
        match_threshold,
        graph,
        limit_count,
    );
    JsonB(json!([]))
}

#[pg_extern]
fn lance_import_column(
    reloid: pg_sys::Oid,
    pk_col: &str,
    vec_col: &str,
    dim: i32,
    lance_path: &str,
) -> i64 {
    let _ = (reloid, pk_col, vec_col, dim, lance_path);
    removed("lance_import_column")
}

#[pg_extern]
fn lance_enable(reloid: pg_sys::Oid, vec_col: &str, dim: i32, lance_url: &str) -> i64 {
    let _ = (reloid, vec_col, dim, lance_url);
    removed("lance_enable")
}

#[pg_extern]
fn knn(reloid: pg_sys::Oid, query: Vec<f32>, k: i32) -> JsonB {
    let _ = (reloid, query, k);
    removed("knn")
}

#[pg_extern]
fn lance_build_index(path: &str, column: &str, num_partitions: i32, num_sub_vectors: i32) -> i64 {
    let _ = (path, column, num_partitions, num_sub_vectors);
    removed("lance_build_index")
}

#!/usr/bin/env bash
# Focused, repeatable DataFusion 55 scale sweep. It keeps the standard
# ClickBench loader/query path, but narrows the systems and queries to the
# routes and shapes that expose TopK, runtime pruning, and Vortex behavior.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

SCALES="${BENCH_SCALES:-100000,1000000,10000000}"
SYSTEMS="${BENCH_SYSTEMS:-rvbbit,rvbbit_native_forced,rvbbit_datafusion_forced,rvbbit_datafusion_vortex_forced}"
QUERIES="${BENCH_QUERIES:-Q8,Q24,Q25,Q26,Q33,Q36}"
REPEATS="${BENCH_REPEATS:-2}"
TIMEOUT="${BENCH_TIMEOUT:-300}"
TEST_NAME="${BENCH_TEST_NAME:-df55-topk-pruning-vortex}"
REBUILD="${BENCH_REBUILD:-0}"

IFS=',' read -r -a scale_values <<< "${SCALES}"
first=1
for scale in "${scale_values[@]}"; do
    scale="${scale//[[:space:]]/}"
    [ -n "${scale}" ] || continue
    args=(--test-name "${TEST_NAME}")
    if [ "${first}" = "1" ] && [ "${REBUILD}" = "1" ]; then
        args+=(--rebuild)
    fi
    echo "== ClickBench DF55 matrix: ${scale} rows =="
    BENCH_LIMIT="${scale}" \
    BENCH_SYSTEMS="${SYSTEMS}" \
    BENCH_QUERIES="${QUERIES}" \
    BENCH_REPEATS="${REPEATS}" \
    BENCH_TIMEOUT="${TIMEOUT}" \
    BENCH_REPORT_DF55_OBSERVABILITY=1 \
        "${SCRIPT_DIR}/run_offline.sh" "${args[@]}"
    first=0
done

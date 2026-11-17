// Size listings must never wait on a table being rewritten (src/lib/db/relation-size.ts).
// Run: node scripts/test-relation-size.mjs            (contract only)
//      PSQL="docker exec -i <pg container> psql -U postgres -d <db>" node scripts/test-relation-size.mjs   (+ live lock test)
import assert from "node:assert/strict"
import { execSync, spawn } from "node:child_process"
import { readFileSync } from "node:fs"
import { BUSY_RELATIONS_CTE, safeHeapSize, safeIndexSize, safeToastSize, safeTotalSize } from "../src/lib/db/relation-size.ts"

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8")
// The three listings that run unattended (stats poll, Finder sizes, routing candidates) use the safe fragments.
for (const [file, bare] of [["src/lib/db/pg-stats.ts", /TOP_TABLES_SQL = `[^`]*pg_total_relation_size/],
  ["src/lib/db/finder-vitals.ts", /SIZE_QUERY = `[^`]*\n\s+pg_(total_)?relation_size/], ["src/lib/rvbbit/routing.ts", /"[^"]*pg_total_relation_size\(c\.oid\)/]]) {
  const source = read(file)
  assert.match(source, /BUSY_RELATIONS_CTE/, `${file} lists busy relations`)
  assert.ok(!bare.test(source), `${file} still calls a size function without the busy guard`)
}
assert.match(read("src/lib/db/pool.ts"), /lane === "meta" \|\| lane === "observer" \? \{ lock_timeout: META_LOCK_TIMEOUT_MS \}/)
for (const fragment of [safeTotalSize("c"), safeHeapSize("c"), safeIndexSize("c"), safeToastSize("c")]) {
  assert.match(fragment, /CASE WHEN/); assert.match(fragment, /busy_relations/); assert.match(fragment, /relpages/)
}
assert.match(safeTotalSize("t"), /t\.reltoastrelid/, "the alias is honoured")

const psql = process.env.PSQL
if (psql) {
  const run = (sql) => execSync(`${psql} -XAt -v ON_ERROR_STOP=1`, { input: sql, encoding: "utf8", stdio: ["pipe", "pipe", "pipe"] }).trim()
  run(`DROP TABLE IF EXISTS public.relation_size_probe; CREATE TABLE public.relation_size_probe AS SELECT g AS id, repeat('x', 200) AS pad FROM generate_series(1, 20000) g;
       CREATE INDEX ON public.relation_size_probe (id); ANALYZE public.relation_size_probe;`)
  const listing = `SET lock_timeout = '2s'; WITH ${BUSY_RELATIONS_CTE} SELECT ${safeTotalSize("c")} || '|' || ${safeHeapSize("c")} || '|' || ${safeIndexSize("c")} || '|' || ${safeToastSize("c")}
                   FROM pg_class c WHERE c.oid = 'public.relation_size_probe'::regclass;`
  const [total, heap, index] = run(listing).split("\n").at(-1).split("|").map(Number)
  assert.ok(heap > 4_000_000 && index > 200_000 && total >= heap + index, `exact sizes when nothing is locked: ${total} ${heap} ${index}`)
  // A loader rewriting the table: ACCESS EXCLUSIVE for a few seconds.
  const holder = spawn("sh", ["-c", `${psql} -XAt`], { stdio: ["pipe", "ignore", "ignore"] })
  holder.stdin.end("BEGIN; LOCK TABLE public.relation_size_probe IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(6); COMMIT;\n")
  await new Promise((resolve) => setTimeout(resolve, 1200))
  const started = Date.now()
  const [lockedTotal, lockedHeap, lockedIndex] = run(listing).split("\n").at(-1).split("|").map(Number)
  const waited = Date.now() - started
  assert.ok(waited < 1500, `the listing did not wait for the lock (${waited} ms)`)
  assert.ok(Math.abs(lockedHeap - heap) <= 8192 * 4 && lockedIndex > 0 && lockedTotal >= lockedHeap, `sizes from pg_class while locked: ${lockedTotal} ${lockedHeap} ${lockedIndex}`)
  let failed = ""
  try { run(`SET lock_timeout = '1s'; SELECT pg_total_relation_size('public.relation_size_probe'::regclass);`) } catch (error) { failed = String(error.stderr || error.message) }
  assert.match(failed, /lock timeout/, "the unguarded call is the one that waits")
  await new Promise((resolve) => holder.on("close", resolve))
  run("DROP TABLE public.relation_size_probe;")
  console.log(`live: exact ${total} bytes unlocked; ${lockedTotal} bytes in ${waited} ms while exclusively locked; unguarded call timed out`)
}
console.log("ok")

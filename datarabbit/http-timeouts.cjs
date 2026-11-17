// Loaded through NODE_OPTIONS=--require before Next's standalone server.
//
// Node caps the time to receive an entire request, headers and body, at 300 s
// (`server.requestTimeout`). A multi-GB import upload over a home uplink took
// longer, Node answered 408, Chrome re-sent the whole body on its own, and
// the retry found the import id consumed (a hosted install, 2026-09-20). Uploads
// are chunked now, but nothing served here should be cut off for being a slow
// upload: lift the whole-request cap and keep headersTimeout (60 s) as the
// slow-client guard.
"use strict"
const http = require("node:http")
const https = require("node:https")

function lift(server) {
  if (server && typeof server === "object" && "requestTimeout" in server) server.requestTimeout = 0
  return server
}
for (const mod of [http, https]) {
  const create = mod.createServer
  mod.createServer = function createServerWithoutRequestCap(...args) {
    return lift(create.apply(this, args))
  }
}

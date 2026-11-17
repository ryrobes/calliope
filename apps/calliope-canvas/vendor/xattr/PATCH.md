# Local compatibility patch

Source: xattr 0.2.3 from crates.io, retaining its MIT and Apache-2.0 licenses.

Only `src/sys/mod.rs` differs from upstream source: the `ENOATTR` constant
uses `libc::ENODATA` on Linux and Android, where these error names are aliases.
The BSD/macOS implementation continues to use `libc::ENOATTR`. Recent libc
versions removed the Linux alias; this keeps the published GPUI 0.2.2
dependency tree compiling without changing libc or patching a global cache.

Remove the override when upgrading to dependencies that use a current xattr.

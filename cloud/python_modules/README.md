Cloudflare's `workers-runtime-sdk` 1.9.4 (MIT, see `LICENSE-workers-runtime-sdk`), copied from the locked
dependency in `../uv.lock`. It is committed so the Deploy to Cloudflare button, which runs a plain
`wrangler deploy` without `uv`, ships a Worker that can `import workers`. Refresh it with
`uv sync --locked` followed by `python3 build.py --vendor` after changing the lock.

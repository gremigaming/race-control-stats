# Live league publisher

`/publish` is an authenticated WebSocket endpoint using the existing
`INGEST_TOKEN`. It accepts a small JSON header followed by binary frames for a
race/qualifying save, a `.gmtel.gz` recording, or an encrypted `.gmcls` capture.
Completed files are stored under `DATA_DIR`; raw files and decrypted OCR results
are never written to R2.

Each accepted upload wakes the warm worker. It runs the same `race-stats`
builders as the Pages workflow, first publishes visible results with
`pending: true`, then replaces safety, incident, award, replay and hidden-name
data in a new immutable generation. `league/index.json` is written last and has
no-store cache headers. Pages remains the frontend fallback and its scheduled
build reconciles the archived race files.

The worker uses the private `CLASSIFICATION_KEY_B64` environment value to read
encrypted captures. It must be the base64 encoding of the PEM private key used
as `CLASSIFICATION_KEY` in `race-stats` Actions. It is optional: without it,
results and safety data still publish, and hidden-name joins remain provisional.
During initial server setup, `live/bootstrap.sh` offers a hidden input for this
value. To add it later, add `CLASSIFICATION_KEY_B64=...` to
`/etc/race-live.env`, keep the file at mode `640` and owned by `root:race-live`,
then restart `race-live`.

`GET /status` exposes the current league stage, generation, pending flag, and
last publish time. It does not expose names, uploaded data, or credentials.

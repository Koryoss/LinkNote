# LinkNote Desktop

This Tauri app is the desktop shell for LinkNote.

The production desktop window is configured in `src-tauri/tauri.conf.json` to open the local backend at:

```text
http://127.0.0.1:8000
```

That means the desktop app shows the same `web/gallery.html` experience served by `api_server.py`, including the concept-map recall trace controls.

## Run

From the repository root, start the backend first:

```bash
./run_api.sh
```

Then start the desktop app:

```bash
cd desktop
npm run tauri dev
```

## Recall Trace Visibility

Phase 1 recall trace is implemented in the backend and gallery UI:

- `POST /recall-traces`
- `GET /recall-traces`
- `web/gallery.html` concept-map `설명해보기` panel

No additional desktop-only data model is used. The desktop app should continue to rely on the existing local `user_id` recall trace flow until auth/user database work is planned in a later PR.

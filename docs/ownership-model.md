# LinkNote Ownership Model

LinkNote stores study data under a `data_user_id` namespace. This value is separate from the login account id and is the only value used for ChromaDB chunks, concepts, concept graph, timetable entries, and recall traces.

## Maintainer legacy mapping

The maintainer account `kory124@snu.ac.kr` keeps access to existing uploaded data by preserving the existing `data_user_id` mapping in `data/users.json`. Current preserved mapping:

```json
{
  "email": "kory124@snu.ac.kr",
  "data_user_id": "정유진"
}
```

Do not replace this value unless an explicit migration mapping is provided. Existing lightweight user ids such as `yoojin` or `정유진` are legacy data namespaces.

## New users

New email/password or Google users receive a new UUID-like `data_user_id` by default. They must not share the maintainer namespace and must not receive access to maintainer ChromaDB chunks, concepts, graph data, timetable entries, uploaded file metadata, or recall traces.

## Server-side isolation

Protected API routes derive the active namespace from the bearer token via `current_uid()`. Frontend-provided `user_id` is ignored for protected study APIs and remains only for backward-compatible request shapes.

The active browser UI is `web/gallery.html`. `web/app.js` is a legacy/experimental UI and must not be treated as the ownership model; if it is used accidentally, protected calls still need an `Authorization: Bearer <token>` header.

PDF preview through `/file` uses a query token because browsers cannot attach custom headers to an iframe URL. The backend still resolves that token to `data_user_id`, normalizes the requested filename, rejects path traversal, and checks ChromaDB metadata before serving a file from `data/uploads`.

`link_user_id` is restricted to maintainer/admin migration use. General users cannot claim a legacy namespace by sending `link_user_id` during registration or Google login.

## Migration rule

Do not automatically migrate, delete, reset, or reassign legacy data. Use an explicit migration script or manual mapping when legacy data must be connected to a login account.

## Ownership audit checklist

- Protected APIs must use `Authorization` -> `current_uid()` -> `data_user_id`.
- Frontend-provided `user_id` must not override authenticated ownership.
- `/file` may accept `token` in the query string for iframe preview, but it must verify the requested filename belongs to that token's `data_user_id`.
- Legacy request fields named `user_id` are compatibility-only unless explicitly marked as response data or stored trace metadata.

## Manual preview test checklist

- Maintainer can preview their own uploaded PDF.
- New user can preview their own uploaded PDF.
- New user cannot preview the maintainer's PDF even if the filename is known.
- Missing token fails with 401.
- Invalid token fails with 401.
- `../` path traversal fails.
- Unknown filename returns 404.
- Gallery PDF preview still works.

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

`link_user_id` is restricted to maintainer/admin migration use. General users cannot claim a legacy namespace by sending `link_user_id` during registration or Google login.

## Migration rule

Do not automatically migrate, delete, reset, or reassign legacy data. Use an explicit migration script or manual mapping when legacy data must be connected to a login account.

# LinkNote Architecture Notes

This document records small architecture decisions that affect data ownership and local file access.

## Frontend Entry Points

- `web/gallery.html` is the current main UI served at `/`.
- `web/mypage.html` is the read-only My Page.
- `web/app.js` is a legacy experimental frontend and should not be used for authenticated production flow.

## Ownership For Protected Data

Protected APIs must derive the active learning-data namespace from the auth token:

```text
Authorization token -> current_user/current_uid() -> data_user_id
```

Frontend-provided `user_id` must not control protected data access. Any remaining `user_id` request fields are compatibility-only unless a route explicitly documents them as response/stored metadata.

## Question Modes

The gallery question UI separates search from generated answers:

- `빠른 검색` calls `POST /ask/search`.
- `AI 답변` calls the existing `POST /ask` flow.

`/ask/search` uses `Authorization` -> `current_uid()` -> `data_user_id`, searches only the current user's owned chunks, concepts, and Recall/Learning Memory records, and returns related sources without calling GPT chat/completion. The current implementation is local keyword/metadata search, so it also does not require `OPENAI_API_KEY` for page load or search-only results.

Search-only results are cached in `data/search_cache.json` by `data_user_id`, normalized question, search filter, and scope. Sensitive patient/clinical-looking queries are not cached. Repeating the same owned search can return `from_cache = true`.

Single-document style search uses the selected semester/course/unit/file filter when present. Multi-document search ignores the UI course filter and searches across the current user's owned materials.

## PDF Preview Access

`GET /file` accepts a query token because PDF iframe previews cannot attach custom request headers.

The backend still resolves that query token to the current `data_user_id`, validates the requested filename, checks the user's existing library/chunk metadata for ownership, and only then resolves a physical file in `data/uploads`.

Existing uploads remain in place. The storage layout is not changed:

- metadata may store the original filename, such as `lecture.pdf`
- `data/uploads` may store a UUID-prefixed file, such as `<uuid>_lecture.pdf`

The server may match the exact stored filename or UUID-prefixed suffix only after ownership has been verified. Unknown filenames, missing/invalid tokens, path traversal, and files not owned by the current `data_user_id` must not be served.

## Data Preservation

Ownership hardening must not delete, move, rename, rebuild, re-index, clean up, or migrate existing uploaded files or analysis data.

# Local Voicebox narration

## Runtime contract

Choose **Voicebox** in Connections for keyless LAN speech. Use the server root URL,
not its OpenAI `/v1` suffix. The app discovers profiles through `/profiles`, TTS
engines through `/v1/models`, and generates MP3 through `/v1/audio/speech`.

On DadServer the existing service is `http://192.168.0.87:17600`. The requested
Hamish profile uses LuxTTS; select its actual profile ID from **Refresh voices**
and `luxtts` as the TTS model. The model must already be downloaded in Voicebox.
The app does not change Voicebox profiles, install models, or change its engine
settings.

The operator must explicitly configure:

```text
WITG_VOICEBOX_HOST_ALLOWLIST=192.168.0.87
WITG_VOICEBOX_SPEECH_TIMEOUT_SECONDS=600
```

For Compose, export these before creating the container. The default allowlist is
empty. This allowlist is independent of the model-provider allowlist. Voicebox
requires an allowed hostname resolving only to permitted private LAN addresses;
metadata, loopback, link-local, multicast, unspecified and reserved addresses
remain forbidden. DNS is pinned and redirects are never followed.

Local CPU synthesis can take roughly two minutes for a full saved review. The
speech deadline is bounded, includes DNS and capacity waiting, and cancels the
HTTP request and frees concurrency on expiry. Discovery retains the ordinary
metadata limit. The UI displays **Preparing narration** during generation.

## Migration

1. Confirm both containers are healthy and that Voicebox exposes the expected
   profile and downloaded model. Do not create or substitute a different voice.
2. Preserve the current container image under a unique rollback tag. Back up the
   Unraid XML template and SQLite database with SQLite's online backup API. Keep
   backups on the server, outside the repository; they include encrypted secrets.
   Never copy or print the provider master key.
3. Build the reviewed application at the exact Git revision. For a normal fresh
   install use the main Dockerfile. If dependencies are unchanged, the optional
   `deploy/Dockerfile.app-only` can preserve the exact installed runtime using
   `WITG_RUNTIME_IMAGE=<existing-image-id>` and `VCS_REF=<full-commit-sha>`. Check
   `pyproject.toml` and `uv.lock` against the old runtime before using this overlay.
4. Add the explicit Voicebox allowlist and speech timeout to the native Unraid
   template, retain all other environment/bind/security settings, and recreate
   the app using Unraid's native rebuild mechanism. Do not recreate Voicebox.
5. In Connections choose **Voicebox**, enter the root URL, refresh/select Hamish,
   choose `luxtts` and MP3, preview/test, then save. The provider change clears the
   old ElevenLabs voice key without touching the model settings or credentials.
6. Read `/api/settings` back. Require `voice.provider=voicebox`, the intended
   profile/model, and `voice.api_key_configured=false`. Check the exact running
   image ID and revision label, not merely the mutable image tag.
7. Generate fresh full narration from an existing saved review; retrieve and
   decode the MP3 from the app's audio URL. Re-request it and verify the cache is
   reused. Confirm history still exposes saved narration and browser playback
   advances with a nonzero duration and no media errors.

There is **no automatic ElevenLabs fallback**. A Voicebox outage returns a retryable
narration error without cloud speech requests. ElevenLabs remains only as an
explicit alternative in Connections. No ElevenLabs key is decrypted or forwarded
on the Voicebox path. Failed, redirected, oversized or non-MP3 responses are not
cached.

Existing reviews, ratings, notes and cached ElevenLabs recordings are retained.
Old audio continues to play without any ElevenLabs subscription. It is not
bulk-regenerated; a new narration request uses the current provider and settings.
The model/analysis provider is independent and remains cloud-backed if configured
that way. Switching speech providers does not make the entire app offline.

## Rollback

Retain the pre-change image, XML template and consistent SQLite backup together.
If a full rollback is needed, stop writes to the app, restore that matching set,
and recreate using the native Unraid template. An older app does not understand a
persisted `voicebox` provider, so reverting only the image is insufficient. Restore
neither the master-key file nor Voicebox data as part of an app-only rollback.
Recheck health, settings, saved reviews and playback afterwards. Never bulk-delete
or overwrite live user data to recover a code deployment.

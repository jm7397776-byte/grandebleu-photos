# Grande Bleu Local Social Feed

This folder is the Make.com source for Daangn/Karrot Business Profile and
KakaoTalk Channel posting packages.

## Source

Make.com should fetch this URL every day after the render workflow runs:

```text
https://raw.githubusercontent.com/jm7397776-byte/grandebleu-photos/main/local_posts/today.json
```

## Schedule

- GitHub Actions render: 10:30 KST
- Make.com fetch: 10:45 KST or later

## Make.com Mapping

Use `HTTP - Make a request` with `GET`, parse response enabled, then filter:

```text
publish equals true
```

An operator blueprint is available at:

```text
make_com_daangn_kakao_blueprint.json
```

An import-oriented Make blueprint is available at:

```text
make_com_daangn_kakao_import_blueprint.json
```

That blueprint may create the daily scheduler and HTTP fetch module depending
on the Make account's available built-in modules. Run it once inside Make so
the later Telegram/queue modules can map the parsed JSON fields.

If Make shows `Module Not Found` for the imported scheduler, use:

```text
make_com_daangn_kakao_http_only_blueprint.json
```

Then set the schedule from Make's bottom scheduling control.

## Live Make Scenario

As of 2026-05-29, the separate Make scenario exists as:

```text
Grande Bleu — Daangn Kakao Daily Package
https://us2.make.com/336756/scenarios/5225849/edit
```

Verified in Make:

- HTTP fetch from `local_posts/today.json`: success
- HTTP status: `200`
- Response size: `4579`
- Schedule saved: daily at 10:45 Asia/Seoul
- Activation: blocked by Make Free plan active scenario limit `2/2`

Do not deactivate the existing GBP or Pinterest scenarios without explicit
operator approval.

### Daangn/Karrot Route

Use these fields for the business profile post package:

```text
channels.daangn.title
channels.daangn.body
channels.daangn.image_urls[]
channels.daangn.cta_url
channels.daangn.hashtags[]
channels.daangn.compliance_note
```

### KakaoTalk Channel Route

Use these fields for the KakaoTalk Channel package:

```text
channels.kakao_channel.title
channels.kakao_channel.body
channels.kakao_channel.image_url
channels.kakao_channel.image_urls[]
channels.kakao_channel.button_title
channels.kakao_channel.button_url
channels.kakao_channel.compliance_note
```

## Current Posting Mode

Both channels are generated in safe package mode:

```text
channels.*.api_direct_supported = false
```

That means Make should first notify or queue the post package. Replace the last
module with an official HTTP connector only if Daangn/Kakao grants a real
posting API for the business account.

## Quality Gates

The renderer rotates topic keys and avoids recently used image URLs stored in
`history.json`. A healthy payload has:

- `publish: true`
- one `topic_key`
- three Daangn image URLs
- one or two Kakao image URLs
- one booking URL per channel

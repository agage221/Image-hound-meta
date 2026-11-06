# image-hound-meta

Instagram manager for Image Hound, built on Meta's own Instagram API (free). It:

- **publishes** posts from a queue in this repo at the times you set (feed images, carousels, reels, stories)
- **checks** every queued post before it goes out (sizes, shapes, file types, caption limits)
- **pulls insights** daily: account reach, views, followers, and per-post views, reach, likes, saves, shares
- **keeps the token alive** by refreshing it weekly (tokens otherwise expire after 60 days)

Everything runs as GitHub Actions. No server.

## Queue a post

Make a folder under `posts/` (copy `posts/example-carousel`). The folder name is the post's id, so a date
prefix keeps them in order:

```
posts/2026-11-10-jury-award/
    post.yml
    01.jpg
    02.jpg
```

```yaml
account: imagehound
publish_at: 2026-11-10 09:30      # Eastern time
type: carousel                    # image | carousel | reel | story (guessed if left out)
media: [01.jpg, 02.jpg]
caption: |
  A jury awarded a photographer $X after a builder used her photos.

  #interiordesign #photography
```

Commit it to `main`. The publisher runs every 15 minutes and posts anything whose time has passed, then
writes `status.json` into the folder with the post's link. To hold a post, add `paused: true`.

Rules the checker enforces: images are JPEG, 8 MB or less, between 4:5 (portrait, 1080x1350) and
1.91:1 (landscape); carousels have 2-10 items; captions are 2,200 characters, 30 hashtags and 20
@mentions at most. Run `igmeta prepare` to turn PNGs into JPEGs. Start captions with `caption: |`,
because a line beginning with `#` is otherwise read as a comment and dropped.

Things Meta's API can't do: music, shopping tags, branded-content tags, filters, collaborator invites
on carousels. Post those by hand.

## Commands

```
pip install -e ".[dev]"
igmeta check                 validate every queued post
igmeta queue                 list what's queued and when
igmeta prepare               convert PNG/WebP to JPEG
igmeta publish --dry-run     show what's due and the image URLs Meta would fetch
igmeta whoami                confirm each account's token works
igmeta limit                 API posts used in the last 24 hours (cap is 100)
igmeta insights --out DIR    append today's insights snapshot
```

## One-time setup

1. **Make the Instagram account a Business account** (Instagram app: Settings > Account type and tools).
2. **Create a Meta app.** At developers.facebook.com > My Apps > Create app, choose the use case for
   managing content on Instagram. Under "API setup with Instagram business login", add the Image Hound
   Instagram account and generate an access token. Grant `instagram_business_basic`,
   `instagram_business_content_publish` and `instagram_business_manage_insights`. The app can stay in
   development mode while it only manages your own accounts.
3. **Save the token** as a repo secret named `IG_TOKEN_IMAGEHOUND`
   (Settings > Secrets and variables > Actions).
4. **Let the token refresh itself.** Create a fine-grained GitHub token for this repo with
   "Secrets: read and write" and save it as the secret `GH_PAT`.
5. **Decide where insights live.** This repo is public, so insights aren't saved here. Set the variable
   `INSIGHTS_REPO` (e.g. `agage221/misc-data-archive`) and the secret `INSIGHTS_TOKEN` (a token that can
   push to it). Until then the daily job pulls but doesn't save.
6. **Test.** Actions > Publish due posts > Run workflow (dry run is on by default).

To add another account (e.g. MGI), add it to `accounts.yml` and save its token as `IG_TOKEN_MGI`.

## How images reach Meta

Meta downloads each image from a public URL. Because this repo is public, the publisher uses the
`raw.githubusercontent.com` link to the committed file, pinned to the commit. To use a storage bucket
instead (e.g. Supabase), set the variable `MEDIA_BASE_URL` to a public URL that mirrors this repo's
`posts/` folder.

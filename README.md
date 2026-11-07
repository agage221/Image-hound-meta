# image-hound-meta

Instagram manager for Image Hound, built on Meta's own Instagram API (free). It:

- **publishes** posts from a queue at the times you set (feed images, carousels, reels, stories)
- **checks** every queued post before it goes out (sizes, shapes, file types, caption limits)
- **pulls insights** daily into `insights/`: account reach, views, followers, and per-post views,
  reach, likes, saves, shares
- **keeps the token alive** by refreshing it weekly (tokens otherwise expire after 60 days)

Everything runs as GitHub Actions. Images live in a Supabase Storage bucket, never in this repo.

## Queue a post

Each post has two halves with the same id (a date prefix keeps them in order):

| Where | What |
|---|---|
| this repo: `posts/2026-11-10-jury-award/post.yml` | when, which account, caption, file order |
| Supabase bucket `instagram`: `2026-11-10-jury-award/01.jpg`, `02.jpg` ... | the images |

```yaml
account: imagehound
publish_at: 2026-11-10 09:00      # Eastern time
type: carousel                    # image | carousel | reel | story (guessed if left out)
media: [01.jpg, 02.jpg]
caption: |
  A jury awarded a photographer $X after a builder used her photos.

  #interiordesign #photography
```

Upload the images either in the Supabase dashboard (Storage > instagram > new folder named with the id)
or from your computer: drop them into the local post folder and run `igmeta upload <id>`. Local
image files in `posts/` are git-ignored, so they're never committed.

Commit `post.yml` to `main`. The checker confirms the images are in the bucket; the publisher runs
hourly (at about :05) and posts anything whose time has passed, then writes `status.json` next to
`post.yml` with the post's link. To hold a post, add `paused: true`.

Rules the checker enforces: images are JPEG, 8 MB or less, between 4:5 (portrait, 1080x1350) and
1.91:1 (landscape); carousels have 2-10 items; captions are 2,200 characters, 30 hashtags and 20
@mentions at most. Run `igmeta prepare` to turn local PNGs into JPEGs before uploading. Start captions
with `caption: |`, because a line beginning with `#` is otherwise read as a comment and dropped.

Things Meta's API can't do: music, shopping tags, branded-content tags, filters, collaborator invites.
Post those by hand.

## Commands

```
pip install -e ".[dev]"
igmeta check                 validate every queued post (reads images from the bucket)
igmeta queue                 list what's queued and when
igmeta prepare               convert local PNG/WebP to JPEG
igmeta upload [ID]           upload local images to the bucket
igmeta publish --dry-run     show what's due and the image URLs Meta would fetch
igmeta whoami                confirm each account's token works
igmeta limit                 API posts used in the last 24 hours (cap is 100)
igmeta insights --out DIR    append today's insights snapshot
```

Locally, set `SUPABASE_URL` (and `SUPABASE_SERVICE_KEY` to upload) in your shell first.

## One-time setup

**Supabase**
1. Create a project at supabase.com (the free plan is fine to start).
2. Storage > New bucket > name it `instagram` and make it **public** (Meta has to download from it).
3. Settings > API: copy the project URL and the `service_role` key.

**Meta**
4. Make the Instagram account a Business account (Instagram app: Settings > Account type and tools).
5. At developers.facebook.com > My Apps > Create app, choose the use case for managing content on
   Instagram. Under "API setup with Instagram business login", add the Image Hound account and generate
   an access token with `instagram_business_basic`, `instagram_business_content_publish` and
   `instagram_business_manage_insights`. The app can stay in development mode while it only manages
   your own accounts.

**GitHub** (repo Settings > Secrets and variables > Actions)
6. Variables: `SUPABASE_URL` (the project URL). `SUPABASE_BUCKET` only if it isn't `instagram`.
7. Secrets: `IG_TOKEN_IMAGEHOUND` (the Meta token), `SUPABASE_SERVICE_KEY` (the service_role key),
   and `GH_PAT`, a fine-grained GitHub token for this repo with "Secrets: read and write", so the
   weekly refresh can save the renewed Meta token.
8. Test: Actions > Publish due posts > Run workflow (dry run is on by default).

To add another account (e.g. MGI), add it to `accounts.yml` and save its token as `IG_TOKEN_MGI`.

## Running costs

- Meta API: free.
- Supabase: free plan has 1 GB of storage (years of posts). Free projects pause after 7 days without
  database activity, so the daily insights job pings it. On Pro ($25/mo) that never happens.
- GitHub Actions: this repo is private, so runs count toward the account's monthly free minutes. The
  hourly publisher uses about 720 minutes a month; insights and the token refresh add about 40.

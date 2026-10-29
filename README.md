# Instagram Manager · Engine

The data store and the "robot" behind [Instagram Manager](https://github.com/skoomaholic-art/assistant-bot-template). It runs entirely on **GitHub Actions** (free):

- keeps the post queue, decisions and statistics as JSON in this repository
- renders slides and carousels by opening Cover Studio in a headless browser (Playwright)
- builds live **Reels**: background motion, 2.5D depth, animated text, transitions and music (ffmpeg)
- publishes through the **official Instagram API**, only after the owner approved both the text and the final
- collects metrics at 24 h / 72 h / 7 days and runs the "Viral Lab" and the AI editor on a schedule

| Repository | What it is | Where it runs |
|---|---|---|
| [assistant-bot-template](https://github.com/skoomaholic-art/assistant-bot-template) | Telegram bot, Mini App, Cover Studio, AI drafts | Vercel |
| **insta-engine-template** (this one) | Data + rendering + publishing robot | GitHub Actions |

> Start with the **assistant-bot** README (steps 1–6). This README is step 7: connecting Instagram and turning the robot on. A Russian illustrated PDF guide is in `assistant-bot/guide/setup-guide.pdf`.

---

## How the two halves talk

```
Mini App / bot (Vercel) ──writes posts.json via GitHub API (IGM_BRIDGE_TOKEN)──► this repo
this repo: push on posts.json ──► Actions "Post Pipeline" ──► renders / publishes
Actions ──signed callback (PIPELINE_SECRET)──► Vercel /api/pipeline-hook ──► final card in Telegram
```

Status flow of a post: `text_review → rendering → final_review → (scheduled) → publishing → published`; also `rejected`, `failed`, `handed_off` ("I'll publish myself").

---

## Setup

### 1. Create your private copy
**Use this template → Create a new repository** → name `insta-engine` → **Private**. (Skip if you did it in the assistant-bot steps.)

### 2. Get Instagram API access (Instagram API with Instagram Login)
Requirements: an Instagram **Professional** account (Business or Creator). Switch in the Instagram app: Settings → Account type and tools → Switch to professional account. A Facebook Page is **not** required for this flow.

1. [developers.facebook.com/apps](https://developers.facebook.com/apps) → **Create app** → use case **Other** → type **Business**
2. Add the product **Instagram** → **API setup with Instagram business login**
3. **Generate access tokens** → **Add account** → log in to your Instagram account and allow:
   - `instagram_business_basic`
   - `instagram_business_content_publish`
   - `instagram_business_manage_insights`
   - (optional) `instagram_business_manage_comments`
4. Copy the token (`IG…`, long-lived, about **60 days**) → **`IG_ACCESS_TOKEN`**
5. Copy the numeric account ID shown next to the account → **`IG_USER_ID`**

The app can stay in Development mode: you only publish to your own account, which is a tester of your own app.
Set a reminder to regenerate the token every ~50 days.

### 3. Secrets and variables
Your repo → **Settings → Secrets and variables → Actions**.

**Secrets** (tab *Secrets*):

| Secret | Value |
|---|---|
| `IG_ACCESS_TOKEN` | token from step 2 |
| `IG_USER_ID` | numeric account ID from step 2 |
| `PIPELINE_SECRET` | **exactly** the same as in Vercel |
| `TG_BOT_TOKEN` | your bot token (used for direct notifications and file hand-off) |
| `TG_OWNER_CHAT_ID` | your numeric Telegram ID |

**Variables** (tab *Variables*):

| Variable | Value |
|---|---|
| `PIPELINE_BASE_URL` | `https://YOUR-DOMAIN.vercel.app` (no trailing slash) |
| `IG_EXPECTED_USERNAME` | your Instagram username without `@` — a safety check: the robot refuses to publish to any other account |
| `IGM_ENABLE_PUBLISH` | leave **unset** (or `false`) for the first runs; set `true` when you are ready for real publishing |

### 4. Enable Actions and run the first workflows
1. **Actions** tab → *I understand my workflows, go ahead and enable them*
2. Run **Music library for Reels** once (*Run workflow*): it downloads a free CC BY music library into `media/music/`
3. Run **Post Pipeline** once manually — it should finish green with "nothing to do"

Scheduled workflows in a fresh copy often don't start until each has been run manually once.

### 5. Make the brand yours
Replace `MyBrand` / `MYBRAND` / `mybrand` across the repo (press `.` on GitHub → Ctrl+Shift+H). Then edit `brand/profile-expected.json` (your username, name, bio).

---

## Workflows

| Workflow | When | What it does |
|---|---|---|
| **Post Pipeline** (`pipeline.yml`) | push to `content/posts.json`, hourly for scheduled posts, manual | Renders approved posts, sends finals, publishes approved finals, publishes scheduled posts when due, hands files off |
| **Viral Lab** (`lab.yml`) | every 6 h | Pulls metrics at 24 h / 72 h / 7 d and compares formats, genres, hooks and visuals |
| **AI Editor** (`editor.yml`) | 3× a day | Asks the bot to prepare a new draft from fresh news (always waits for your approval) |
| **Music library for Reels** (`music-library.yml`) | manual | Downloads/refreshes the music library |
| **Instagram Manager** (`instagram-manager.yml`) | issues titled `[IGM] …` | Owner commands such as `replan-prompts` |
| Safety / layout tests | on push | Checks that nothing can be published without two approvals, and that Cover Studio renders |

---

## Reels engine

Each slide is rendered in Cover Studio as two layers — background (JPEG) and text + logo (transparent PNG) — then assembled by ffmpeg at 1080×1920, 30 fps, up to 90 s.

- **Scenes:** a still image with camera motion (zoom in/out, pan), a **2.5D** clip (MiDaS small depth model on CPU — near objects move more than far ones), or **your own video clip** (reply to the Reels card in Telegram with a video)
- **Text:** the first scene's text is visible from frame 0 (Instagram uses it for the preview); later scenes fade/slide in
- **Styles** (picked by the AI from the topic, changeable from the card): `dynamic`, `calm`, `epic`, `thriller`, `nostalgic`, `mystery` — each sets scene pace, transitions, motion strength and music mood
- **Music:** picked by mood from `content/music.json`; fades in/out; the author credit is added to the caption automatically. You can add your own tracks by sending audio to the bot
- The MiDaS model (~65 MB) is downloaded from its official GitHub release on first use and cached between runs. If it is unavailable the robot falls back to regular motion

The Instagram API cannot attach Instagram's in-app music library to posts. Use the bundled library, your own licensed tracks, or choose **📥 I'll publish myself** and add music in the Instagram app.

---

## Scheduled publishing
Pick a time when creating a post or on the final card (**🕒 Time**). Times are whole hours, 08:00–23:00 in the bot's time zone, from 30 minutes to 30 days ahead. After the final approval the post becomes `scheduled`; the hourly run publishes it when due. You can **Publish now** or reject it until then.

---

## Troubleshooting

| You see | Most likely | Do this |
|---|---|---|
| "Vercel rejected the signature" | `PIPELINE_SECRET` differs from Vercel | Make them identical, Redeploy Vercel, re-run the workflow |
| Instagram `(#10)` / "Application does not have permission" | Missing `instagram_business_content_publish` | Regenerate the token with all permissions from step 2 |
| Instagram `(#200)` | Account is not Professional, or the token was issued for another account | Switch to Professional; regenerate for the right account |
| Instagram `190` / "Error validating access token" | Token expired (~60 days) or revoked | Generate a new one, update `IG_ACCESS_TOKEN` |
| Error subcode `33` in the Lab | The post was deleted in Instagram | Nothing to do — it is marked as missing |
| "Username mismatch" | `IG_EXPECTED_USERNAME` ≠ the token's account | Fix the variable (no `@`) |
| Final arrives but nothing is published | `IGM_ENABLE_PUBLISH` is not `true` | Set it to `true` |
| `ffmpeg: command not found` | Runner image changed | Workflows install it automatically — re-run; if it persists, check the step that installs ffmpeg |
| Schedules never run | New copies need a first manual run; GitHub also pauses schedules after 60 days without commits | Run each workflow once with *Run workflow* |
| Image fetch error for a still | The image host isn't allowed or returned HTML | Only `image.tmdb.org` and `assets.fanart.tv` are allowed; pick another still |
| Run takes long on the first Reels | Model and dependencies are downloaded | Normal once; later runs use the cache |

Open the red run in **Actions** to see the exact step that failed. Never paste tokens into issues or logs.

---

## Costs
GitHub Actions minutes for a private repo on the free plan (2,000 min/month) are plenty: a carousel takes ~2–3 min, a Reels ~4–6 min. The Instagram API, TMDB and the music library are free.

## Credits & license
- Music: Kevin MacLeod ([incompetech.com](https://incompetech.com)), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — credited in captions automatically
- Depth model: [MiDaS](https://github.com/isl-org/MiDaS) (MIT)
- Movie stills: © their rights holders, via the TMDB / fanart.tv APIs; used only after the owner's confirmation. This product uses the TMDB API but is not endorsed or certified by TMDB
- Code: MIT — see [LICENSE](LICENSE)

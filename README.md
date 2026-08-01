# Athenaeum Admin Portal

A local Flask admin portal for the Athenaeum Supabase backend. It talks to the
database with the **service-role key**, so it runs on `127.0.0.1` only.

## Running it

```bash
venv\Scripts\activate
pip install -r requirements.txt
python app.py                 # http://127.0.0.1:5000
```

No login by default - the dashboard opens straight away.

### Environment (`.env`)

| Variable | Purpose |
| --- | --- |
| `SUPABASE_URL` / `BASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Service-role key (bypasses RLS - keep it local) |
| `REQUIRE_LOGIN` | `false` (default) skips the login screen |
| `SECRET_KEY` | Signs the session cookie |
| `ADMIN_EMAIL` | The one account that can sign in |
| `ADMIN_PASSWORD` | Its password - required whenever the login screen is on |
| `PER_PAGE` | Rows per page in list views (default 25) |

### Login

The portal has its own single credential, `ADMIN_EMAIL` + `ADMIN_PASSWORD`. It
does **not** use Supabase Auth: signing in here never touches the accounts the
student app uses, and changing this password has no effect on anyone's app
login.

The email is fixed, so the login screen only asks for the password. To change
the password, edit `ADMIN_PASSWORD` in `.env` (and in the Vercel environment
variables) - no database change involved.

Locally the screen is skipped entirely. **On Vercel it is always on**, and the
app refuses to start if `ADMIN_PASSWORD` is empty, so the form can never let a
blank password through.

The `promote-admin` CLI still exists, but it only sets `profiles.role` for the
student app - it has nothing to do with getting into this portal.

## Deploying to Vercel

```bash
npm i -g vercel
vercel            # preview
vercel --prod     # production
```

`api/index.py` is the entry point, `vercel.json` routes every request to it,
and `.vercelignore` keeps `.env` and `venv/` out of the upload.

### Environment variables to set in the Vercel dashboard

| Variable | Value |
| --- | --- |
| `SUPABASE_URL` | your project URL |
| `SUPABASE_SERVICE_KEY` | the service-role key |
| `SECRET_KEY` | **required** - `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_PASSWORD` | **required** - the portal password, same value as in `.env` |
| `ADMIN_EMAIL` | optional, defaults to `athenaeum.institute@gmail.com` |

`SECRET_KEY` is required because every serverless instance signs session
cookies with it; without a shared value, sign-ins stop sticking. Both it and
`ADMIN_PASSWORD` are checked at boot, so a missing value fails immediately with
a clear message instead of silently at sign-in.

### Signing in

The login screen asks only for the password, since the email is fixed. Copy
`ADMIN_PASSWORD` from `.env` into the Vercel environment variables and use the
same value there.

### What the hosted build sets automatically

* The login screen is on (locally it stays off).
* The session cookie is marked `Secure`, since Vercel serves over HTTPS.
* `ProxyFix` reads Vercel's `X-Forwarded-*` headers so the app sees the real
  scheme and host.
* Static files get a 24-hour cache header, so they don't cost an invocation on
  every page load.

### Serverless notes

* Functions are stateless. Sessions are signed cookies, so logins survive
  between invocations; nothing else is kept in memory.
* `maxDuration` is 60s, which only matters for a broadcast to a very large
  audience (one notification row per recipient).

## What the portal does

| Section | Capabilities |
| --- | --- |
| **Dashboard** | Live counts, revenue from paid enrollments, 14-day enrollment/sign-up trend, 7-day AI usage, role split, upcoming classes, pending trial requests |
| **Users** | Search/filter by role, status and plan; create account (Auth + profile); edit profile; change role, plan, XP, streak; block/unblock (also bans the Auth user); reset password; enrol in a course; send a notification; delete user (owned rows removed, courses/exams/classes detached) |
| **Teachers** | Roster with course, student, class and exam counts; create a teacher account; promote an existing user; assign/unassign courses; list of courses with no instructor; per-teacher view of courses, live classes, exams, materials and redeemed access codes; notify, block, demote or make admin |
| **Payments** | Approval queue for pending enrollments: verify the transaction reference, approve (notifies the student and can lift them off the trial plan), reject (removes access and explains why), revert an approval, bulk approve, pending/approved/monthly totals, CSV export |
| **Courses** | CRUD, publish/unpublish, instructor assignment, learning outcomes; per-course modules and lessons (ordering, video URL, duration, free-preview); materials; enrolled students; linked exams and live classes |
| **Exams** | CRUD, publish/unpublish, question bank (4 options + correct answer + marks, total marks recalculated automatically), results with averages and pass counts, record an attempt by hand, regrade a score, clear an attempt so a student can retake |
| **Progress** | Per-course completion for each student (lessons done, %, last activity) on the user page, a progress column per student on the course page, mark a whole course complete, or reset progress |
| **Family links** | Link a student to a parent, verify or unverify the link, unlink; writes both `parent_child_links` and `profiles.parent_id` so the app sees it either way |
| **Enrollments** | Create, change payment status and transaction reference, delete, filter by course/status, search by student, CSV export |
| **Live Classes** | Schedule with auto-generated Jitsi room, edit, mark live/ended, open the room, delete |
| **Community** | Approve/hide, pin, edit and delete posts; publish as admin; reply to a post; edit or delete comments; wipe a gamed like count |
| **Carts** | Abandoned baskets with their value, flagged when the student has since enrolled; convert a basket item into a pending enrollment, remove one, or purge stale ones |
| **AI usage** | Per-user daily counters with an adjust dialog and a clear action that hands back the quota immediately |
| **Announcements** | CRUD, audience targeting, type, pin, expiry, archive; optionally push the same text as a notification |
| **Notifications** | Broadcast to an audience (all/paid/trial/teachers/parents), browse, delete, purge read |
| **Trial Requests** | Log a phone/WhatsApp enquiry, track pending → contacted → scheduled → converted/rejected, CSV export |
| **Access Codes** | Generate in bulk, reset a redeemed code, delete, purge used |
| **Reports** | Revenue by course, XP leaderboard, heaviest AI users, course mix, CSV export of **all 21 tables** |

### Table coverage

Every table in `schema.sql` is reachable. Two deliberate exceptions:

* `cart_items` has no update - a row is only `(user_id, course_id, added_at)`,
  so a change is a delete plus an add.
* `post_likes` has no create/update - an admin manufacturing likes on behalf of
  students would be fabricated data. It is counted, and can be wiped.

## Layout

```
app.py                 app factory, template filters, CLI commands
config.py              environment configuration
extensions.py          the shared Supabase client
security.py            session auth, CSRF, blueprint guard
blueprints/            one module per section of the portal
services/db.py         query/pagination/CRUD helpers over PostgREST
services/forms.py      form value coercion (empty string -> NULL, etc.)
services/auth_users.py Supabase auth admin helpers (paged)
templates/             Jinja templates; _macros.html holds shared UI pieces
static/                stylesheet and the small JS layer (modals, tabs, confirms)
```

## Notes on the data

* `profiles.role` is stored with inconsistent casing (`student` and `Student`,
  `Teacher`, `Admin`). Role filters use case-insensitive matching so counts are
  correct; the role dropdown writes lowercase values.
* `enrollments.student_id`, `notifications.user_id` and friends reference
  `auth.users`, not `public.profiles`, so PostgREST cannot embed the profile.
  Those joins are done in Python (`services/db.attach_profiles`).
* Database triggers already do some of the work, so the portal must not repeat it:
  scheduling a live class inserts notification rows on its own, and
  `community_posts.comments_count` / `likes_count` are maintained automatically
  when comments and likes are inserted or deleted.
* `user_progress` rows reference a lesson, and a lesson only knows its module, so
  course-level progress is rebuilt through lesson -> module -> course in
  `services/progress.py`.
* Family relationships exist twice in the schema (`profiles.parent_id` and
  `parent_child_links`). The portal writes both and shows either, so a link made
  through one mechanism is never invisible.
* There is no payments table. A payment *is* an `enrollments` row:
  `payment_status` moves `pending -> paid` and `transaction_id` holds the
  reference the student submitted. Rejecting deletes the row, which is what
  actually revokes course access.
* Deleting a course removes its modules, lessons, enrollments, cart items,
  materials and access codes first. Exams, live classes and announcements that
  point at the course must be detached manually - the delete will report the
  constraint instead of cascading silently.

## Security

* The dev server binds to `127.0.0.1`, so nothing on the network can reach it.
* All state-changing routes are POST and carry a per-session CSRF token, which
  also stops a random web page you visit from driving the portal in the
  background.
* `REQUIRE_LOGIN=true` re-enables the login gate on every blueprint. Sign-in
  verifies the password against GoTrue over a direct HTTP call rather than
  `supabase.auth.sign_in_with_password()`, which would otherwise replace the
  shared client's service-role header with the signed-in user's JWT.
* `.env` holds the service-role key and is git-ignored - keep it that way.

# VK publisher on the VPS

The canonical queue is `/var/lib/void-vk-publisher/queue`; the authorized Chromium profile is `/var/lib/void-vk-publisher/profile`. Naz and VOID are content producers. The standalone `vk_queue_consumer.py` is the only browser process and never imports `main.py`, Telegram, or LLM code.

## Users, group and directories

Substitute the actual service account names if they are not literally `naz` and `void`.

```sh
sudo groupadd --system vkqueue
sudo useradd --system --create-home --shell /usr/sbin/nologin publisher
sudo usermod -aG vkqueue publisher
sudo usermod -aG vkqueue naz
sudo usermod -aG vkqueue void

sudo install -d -o publisher -g vkqueue -m 0710 /var/lib/void-vk-publisher
sudo install -d -o publisher -g vkqueue -m 0710 /var/lib/void-vk-publisher/queue
sudo install -d -o publisher -g vkqueue -m 3730 /var/lib/void-vk-publisher/queue/pending
sudo install -d -o publisher -g publisher -m 0700 /var/lib/void-vk-publisher/queue/processing
sudo install -d -o publisher -g publisher -m 0700 /var/lib/void-vk-publisher/queue/done
sudo install -d -o publisher -g publisher -m 0700 /var/lib/void-vk-publisher/queue/failed
sudo install -d -o publisher -g publisher -m 0700 /var/lib/void-vk-publisher/profile
sudo install -d -o publisher -g publisher -m 0700 /var/cache/void-vk-publisher
```

`3730` gives `pending` setgid + sticky and group write/execute without directory listing. Producer processes use `UMask=0027`; job directories are `0770`, while `job.json` and media are `0640`, inheriting `vkqueue`. The sticky bit prevents producers deleting or renaming another owner’s job directory.

For a stricter write-only inbox, use default ACLs and remove producer access immediately after close/rename. Example (verify on the VPS filesystem before enabling services):

```sh
sudo setfacl -m g:vkqueue:-wx,m::rwx /var/lib/void-vk-publisher/queue/pending
sudo setfacl -d -m u::rwx,g::r-x,g:vkqueue:r-x,m::rwx,o::--- /var/lib/void-vk-publisher/queue/pending
```

The bots must be members of `vkqueue` only, never of `publisher`. Profile ownership stays `publisher:publisher 0700`; bots cannot traverse or read it.

## Application and systemd

```sh
sudo /opt/void_entity/venv/bin/pip install -r /opt/void_entity/requirements.txt
sudo -u publisher /opt/void_entity/venv/bin/python -m playwright install chromium
sudo install -o root -g publisher -m 0640 deploy/void-vk-publisher.env.example /etc/void-vk-publisher.env
sudo install -o root -g root -m 0644 deploy/systemd/void-vk-autopost.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/systemd/void-vk-autopost.timer /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/systemd/void-vk-producer.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/systemd/void-vk-producer.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now void-vk-autopost.timer void-vk-producer.timer
```

The consumer timer only consumes. The producer timer invokes LLM/media generation and atomically enqueues a `producer=void` job; it never opens Chromium.

## One-time authorization

Temporarily give `publisher` an interactive shell and run `python vk_browser_publisher.py login` in a VPS desktop/VNC session with `VK_BROWSER_PROFILE_DIR=/var/lib/void-vk-publisher/profile`. Complete VK login, verify the configured community, close Chromium, restore `/usr/sbin/nologin`, then enforce `publisher:publisher 0700` again. Never copy cookies or profile data to either bot.

## Operations

Consumer kill switch:

```sh
sudo touch /etc/void-vk-publisher.disabled
sudo rm /etc/void-vk-publisher.disabled
```

Explicit admin requeue preserves the original `dedupe_key` and moves the same failed directory back to pending:

```sh
sudo -u publisher sh -c 'set -a; . /etc/void-vk-publisher.env; exec /opt/void_entity/venv/bin/python /opt/void_entity/vk_queue_consumer.py requeue-failed JOB_ID'
```

Do not manufacture a replacement job to retry a failed dedupe key. Inspect status with `systemctl status` and sanitized errors with `journalctl -u void-vk-autopost.service`.

Composer discovery uses bounded data/role/ARIA/contenteditable selectors and one safe page reload. A temporary absence stays pending under the existing backoff. An expired browser session is terminal and creates a metadata-only `admin-notices/<job_id>.json` notice; it never stores credentials, cookies, post text, or profile contents. Existing failed jobs are never requeued automatically.

Retryable failures exit `75` and persist their sanitized class in `retry.json`; they are no longer reported to systemd as successful runs. If an exact VOID audio result is absent, that track is durably removed from the active rotation and the same job advances to the next fresh active catalog track on the next safe run. It is not published without music and the missing track is not recorded as played. Other failures remain bounded by `VK_MAX_PUBLISH_RETRIES` (default `12`). A later administrative `requeue-failed` clears ordinary retry state. If the consumer is killed while a job is in `processing`, the next locked run reconciles it with durable publication receipts before deciding between `done` and `pending`.

The shared `recent-tracks.json` is a full least-recently-used history, not an eight-post window. Keep `VK_TRACK_ROTATION_SIZE` equal to the source VOID allowlist size (currently `149`) and `VK_MUSIC_TRACKS_FILE` pointed at that exact catalog. `unavailable-tracks.json` is the reversible active-catalog quarantine; rotation depth is computed from the remaining available catalog, so one deleted VK result cannot deadlock rollover. Naz retains its independent smaller-catalog policy. Confirmed historical jobs and their actual attached track are backfilled from receipts before publication.

Before clicking Publish, the consumer verifies that a new matching audio attachment appeared in the composer and writes `.publication-attempt-unresolved.json` atomically. A normal receipt clears that marker on the next run. If no new wall post with the expected text and audio can be proven, the marker and metadata-only admin notice remain and all later runs exit `75`; inspect the named job and the VK wall before reconciling it. Never delete this marker and requeue the job until you have confirmed that the original click did not publish a post.

The selector contract is covered by static and mock-DOM tests. A restored composer is cleared only when its normalized text belongs to a managed pending, processing, or failed job. The consumer checks the actual visible attachment-item count is zero before upload and exactly matches the job afterward; an unrelated manual draft causes an admin notice and a safe stop. When a live browser session is unavailable during development, final DOM compatibility can only be confirmed read-only on the authorized community page or by the next natural consumer run; no test post is created for that check.

Before a coordinated Naz/VOID deploy, pipe the resolved schedule-only JSON snapshot to `python deploy_preflight.py --resolved-schedules -`. The preflight reads no env or queue payloads, checks the exact systemd units, both runtime marker schemas, queue processing state and the consumer lock, and exits `75` while any natural slot embargo or in-flight work is present. Stdin avoids leaving a schedule snapshot on disk.

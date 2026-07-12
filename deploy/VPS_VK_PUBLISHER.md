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

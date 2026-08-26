# VOID VK community conversation adapter

This service answers private messages sent to `club237593988` and can optionally
reply to direct questions or VOID invocations in new wall comments and
user-authored wall posts. It uses VK Bots Long Poll, so there is no public
callback endpoint, web server, confirmation secret, or inbound firewall rule.
It is independent from the browser-based wall publisher and its API client has
no `wall.post`, edit, or delete capability.

## Required VK setup

1. In community management, enable **Messages** for the community.
2. In **API usage -> Long Poll API**, enable Long Poll and subscribe to
   `message_new`. For public replies also subscribe to `wall_reply_new` and
   `wall_post_new`; edit/delete lifecycle events are intentionally non-actionable.
3. Create a dedicated **community access token** with message access. Add wall
   access only when public replies are enabled, and community-management access
   when VK requires it for Long Poll. Do not grant photo, document, story, or
   market access and do not use the personal `VK_USER_ACCESS_TOKEN` used by older
   publishing tools.
4. Put that token in `/etc/void-vk-community.env` as
   `VK_GROUP_ACCESS_TOKEN`. Keep the file `root:void` and mode `0640`.
5. Keep both `VK_COMMUNITY_BOT_GROUP_ID` and
   `VK_COMMUNITY_ALLOWED_GROUP_IDS` equal to `237593988`. The process refuses a
   multi-community allowlist.

For a private pilot, set `VK_COMMUNITY_ALLOWED_USER_IDS` to the comma-separated
VK IDs of testers. Leave it empty only when replies should be open to everyone.
The allowlist applies to both private messages and public wall activity. Public
replies additionally require `VK_COMMUNITY_PUBLIC_REPLIES_ENABLED=true`; plain
comments without a question or direct VOID invocation remain untouched.

`VK_COMMUNITY_WELCOME_ENABLED=true` enables one static first-contact message for
every new private writer, independently of the dialogue allowlist. Put the
reviewed copy and HTTPS destinations in `VK_COMMUNITY_WELCOME_TEXT`, encoding
line breaks as `\n`. Delivery uses the inbound event's stable `random_id`; the
contact becomes `sent` only after VK accepts `messages.send`. Existing private
correspondents are backfilled as already known during migration, and a closed
user receives the welcome without gaining model access.

## Safe installation

The VPS runs immutable release directories. Install the base unit and an exact
release drop-in together; never point this service at a mutable checkout. In the
commands below, replace both paths with the release being deployed and the
canonical shared database used by that release.

```sh
release_dir=/opt/void_entity_release_YYYYMMDD_COMMIT
character_db=/opt/void_entity_release_20260713/void.db
install -d -o void -g void -m 0700 /var/lib/void-vk-community
install -o root -g void -m 0640 deploy/void-vk-community.env.example /etc/void-vk-community.env
install -o root -g root -m 0644 deploy/systemd/void-vk-community.service /etc/systemd/system/void-vk-community.service
install -d -o root -g root -m 0755 /etc/systemd/system/void-vk-community.service.d
sed -e "s|@RELEASE_DIR@|$release_dir|g" \
    -e "s|@CHARACTER_DB@|$character_db|g" \
    deploy/systemd/void-vk-community-release.conf.example \
    > /etc/systemd/system/void-vk-community.service.d/zz-release.conf
chown root:root /etc/systemd/system/void-vk-community.service.d/zz-release.conf
chmod 0644 /etc/systemd/system/void-vk-community.service.d/zz-release.conf
systemctl daemon-reload
systemd-analyze verify void-vk-community.service
```

The service is intentionally inert at this point. Configure the group and
OpenAI tokens, set `VK_COMMUNITY_BOT_ENABLED=true`, verify the group ID, and only
then create the explicit enable marker:

```sh
install -o root -g root -m 0644 /dev/null /etc/void-vk-community.enabled
systemctl start void-vk-community.service
```

Do not run `systemctl enable`: the unit has no install target on purpose. Add it
to boot only after a successful closed pilot and an explicit operational
decision.

## Health and recovery

The process atomically updates `/var/lib/void-vk-community/status.json` and sends
systemd watchdog heartbeats. Check both layers:

```sh
release_dir=/opt/void_entity_release_YYYYMMDD_COMMIT
systemctl status void-vk-community.service
systemctl show void-vk-community.service -p WorkingDirectory -p ExecStart
$release_dir/venv/bin/python $release_dir/vk_community_bot.py status --json
```

Every accepted Long Poll event is written to `events.sqlite3` before processing.
Event IDs are deduplicated, generated replies survive a restart, and a stable VK
`random_id` makes send retries idempotent. Poison events become `dead` after the
configured retry limit and are reflected in health status.

Dialogue history is stored separately in `dialog.sqlite3`. The service opens the
canonical VOID database only in SQLite read-only mode to read `character_states`;
it never writes VK identities into Telegram dialogue tables.

To stop immediately without touching wall publishing:

```sh
systemctl stop void-vk-community.service
rm /etc/void-vk-community.enabled
```

Removing the marker prevents accidental restarts. Preserve both SQLite files for
dedupe and dialogue continuity; deleting them can cause duplicate replies or
loss of conversation context.

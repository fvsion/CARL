# CARL Reference: Clients on other computers

[Index](README.md) · the client folder's connection file, the dashboard's API, the config push and the sync service.

## The client folder carries the connection

At each server start, the launcher writes two files into `client/`:

| File | Contents | Mode |
|---|---|---|
| `remote.json` | The server's address and port, and the dashboard's API (`http://ADDRESS:PORT+1`) | 0600 |
| `api-key` | The server's API key | 0600 |

- Git ignores both files.
- Copy the client folder to the other computer. Then run `./install-clients.sh && ./install.sh` there. The installer needs no arguments: it reads `remote.json`.
- On the server's own Mac, `remote.json` names an address of this Mac. The installer then works in local mode.
- `CARL_CLIENT_DIR` sets another folder for the two files (the tests use it).

## The dashboard's API

The dashboard serves a small API while it runs (`tools/monitor/cacheapi.py`). It listens on the server's address, on the server's port + 1. It wants the server's API key.

| Request | Use |
|---|---|
| `GET /carl/cache/settings` | The Caching settings |
| `POST /carl/cache/claim`, `/release` | Take a slot for a request, or give it back |
| `POST /carl/cache/record`, `/unrecord`, `GET /carl/cache/record` | Which session a slot holds |
| `POST /carl/cache/save-recorded` | Save the recorded sessions of a model that stops (a router switch) |
| `POST /carl/cache/unpack` | Make a conversation that is stored as a patch whole again, before a restore |
| `GET /carl/client/config` | The pushed client config (with an ETag; 304 when the client has it) |
| `GET /carl/client/events` | Server-sent events: `config` when you push a new config |

- The claims and the records are the same files that the clients on this Mac use. Every client sees the same state.
- The API checks every input: names, slot numbers and the body size.

## Push a config to the clients

The server decides some parts of the client config: the installed models, their windows and the default model. When these change, push them:

1. On the Mac, open the dashboard. Push `2` for the Connect tab.
2. Push `P` (or click **Push config to clients**). Or run `./carl.sh push`.

The dashboard writes the config to `~/.config/carl/client-config.json`, with a version. A push of the same config gives the same version. The clients then do not apply it again.

## The sync service

`install.sh` on another computer adds the sync service (`client/carl-sync.py watch`):

- macOS: a launchd agent, `dev.carl.sync`. Linux: a `systemd --user` unit, `carl-sync.service`.
- Linux: a user service runs only while you are logged in. To keep it running (a VM that you reach by SSH), run `loginctl enable-linger $USER` one time. The installer tells you when lingering is off.
- A test in Docker containers checks the Linux service: `CARL_DOCKER_TESTS=1 python3 -m unittest tests/integration/test_sync_docker.py`.
- The service opens one connection to the dashboard's API and waits for events. It opens no port on the client.
- When a new config arrives, the service writes `installed-models.json` and runs `install.sh` again. The installer uses the switches of your last install (`~/.config/carl/client-install.env`) and makes backups as always.
- When the dashboard is not running, the service tries again: after 5 s, 10 s, 30 s, then each minute.
- `NO_SYNC_SERVICE=1 ./install.sh` removes the service. Without the service, OpenCode and Pi check for a new config one time when they start.

## Auto-apply and the /carl panel

- A new config is applied at once. To keep it waiting, turn auto-apply off: type `/carl` in OpenCode or Pi, select **Config sync**, then **Turn auto-apply off**. Or run `carl-sync.py auto off`.
- With auto-apply off, the panel shows the waiting version and **Apply now**.
- OpenCode and Pi read their configs when they start. After a config is applied, OpenCode shows a message and Pi shows a notice: restart it to use the new config.
- `/carl` also shows every CARL piece on this computer with its state: the prompt cache, the model check, the session switcher, the subagents sidebar, the coder, the browser, web search and LSP.

## The Clients tab

The Connect tab has two sub-tabs: **Setup** and **Clients**. Push `[` or `]` to change between them.

- **Clients** lists this Mac (its OpenCode and Pi configs) and each computer that syncs.
- For each computer: the host name, the user, the system, the address, the service or the start check, connected or the last time seen, and its config against the pushed config.
- The dashboard keeps the list in `~/.config/carl/clients.json`. **Forget clients not seen for a week** removes old rows.

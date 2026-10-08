# CARL Reference: Clients on other computers

[Index](README.md) · the client package, the dashboard API, how the dashboard sends the config, and the sync service.

## The client package carries the connection

At each server start, the launcher writes two files into `client/` (`host/common.sh`, `write_client_package`):

| File | Contents | Mode |
|---|---|---|
| `remote.json` | The server's address and port, the dashboard API (`http://ADDRESS:PORT+1`) and the CARL version | 0600 |
| `api-key` | The server's API key | 0600 |

- Git ignores both files.
- `./carl.sh package` (or `z` in the dashboard's Connect tab) puts them in a zip with the client folder: `dist/carl-client-VERSION-HOST.zip` (`tools/carl_core/adapters/client_package.py`). The file list is `git ls-files client/`, the entry points `setup` and `setup.command`, and the generated files `remote.json`, `api-key`, `installed-models.json` (the installed models now) and `VERSION`. The zip has mode 0600. In the zip, the key file and `remote.json` have mode 0600 and the scripts 0755. `unzip` and the Finder keep these modes.
- `package` refuses a server that serves only this Mac (an address such as 127.0.0.1 in `remote.json`): it writes nothing, and it tells you how to change the network. `--anyway` writes the zip all the same.
- On the other computer: `unzip carl-client-*.zip && cd carl-client && ./setup`. The setup needs no arguments: it reads `remote.json` and `api-key`. It sets the mode of the key file to 0600 again.
- An update is the same: a new zip unzipped over the old folder (`unzip -o`), then `./setup`.
- On the server's own Mac, `remote.json` names an address of this Mac. The setup (`./carl.sh install`) then works in local mode.
- `CARL_CLIENT_DIR` sets another folder for the two files (the tests use it). `package` reads them there too.

## The dashboard's API

The dashboard serves a small API while it runs (`tools/monitor/cacheapi.py`). It listens on the server's address, on the server's port + 1. It wants the server's API key.

| Request | Use |
|---|---|
| `GET /carl/cache/settings` | The Caching settings |
| `POST /carl/cache/claim`, `/release` | Take a slot for a request, or give it back |
| `POST /carl/cache/record`, `/unrecord`, `GET /carl/cache/record` | Which session a slot holds |
| `POST /carl/cache/save-recorded` | Save the recorded sessions of a model that stops (a router switch) |
| `POST /carl/cache/unpack` | Make a saved session that is stored as changes (a patch) whole again, before a restore |
| `GET /carl/client/config` | The pushed client config (with an ETag; 304 when the client has it) |
| `GET /carl/client/events` | Server-sent events: `config` when you push a new config |

- The claims and the records are the same files that the clients on this Mac use. Every client sees the same state.
- The API checks every input: names, slot numbers and the body size.

## Send the config to the clients

The server decides some parts of the client config: the installed models, their contexts and the default model. When these change, send them:

1. On the Mac, open the dashboard. Push `2` for the Connect tab.
2. Push `P` (or click **[ Send the config (P) ]**; in the Clients sub-tab: **[ Send the config to them (P) ]**). Or run `./carl.sh push`.

The dashboard writes the config to `~/.config/carl/client-config.json`, with a version. The same config gives the same version. The clients then do not apply it again.

## The sync service

The setup on another computer adds the sync service (`client/carl-sync.py watch`). Its part `install.sh` does the work:

- macOS: a launchd agent, `dev.carl.sync`. Linux: a `systemd --user` unit, `carl-sync.service`.
- Linux: a user service runs only while you are logged in. To keep it running (a VM that you reach by SSH), run `loginctl enable-linger $USER` one time. The installer tells you when lingering is off.
- A test in Docker containers checks the Linux service and an install from the client package (unzip, `./setup --yes`): `CARL_DOCKER_TESTS=1 python3 -m unittest tests/integration/test_sync_docker.py`.
- The service opens one connection to the dashboard's API and waits for events. It opens no port on the client.
- When a new config arrives, the service writes `installed-models.json` and runs `install.sh` again. It uses the choices of your last setup (`~/.config/carl/client-install.env`: the clients, the coder, web search, LSP, the browser and the other switches) and makes backups as always.
- When the dashboard is not running, the service tries again: after 5 s, 10 s, 30 s, then each minute.
- `NO_SYNC_SERVICE=1 ./setup` removes the service. Without the service, OpenCode and Pi check for a new config one time when they start.

## Apply at once, and the /carl panel

- A new config is applied at once. To keep it waiting, type `/carl` in OpenCode or Pi, select **Config sync**, then **Do not apply new configs at once**. Or run `carl-sync.py auto off`. **Apply new configs at once** (`carl-sync.py auto on`) turns it back on.
- When a new config waits, the panel says "A new config from the dashboard waits." and offers **Apply the new config now**. **Check for a new config now** asks the dashboard.
- The section uses plain sentences, for example "Last config from the dashboard: today 10:53.". Its **Details** part holds the server address, the dashboard API address and the config version. **‹ back** goes back to the list.
- OpenCode and Pi read their configs when they start. After a config is applied, OpenCode shows a message and Pi shows a notice: restart it to use the new config.
- The **Details** part also holds the version of the client package (`VERSION` in the client folder) and the CARL version of the server. When they differ, the section says so: make a new package, unzip it over the folder and run `./setup`.
- The server version comes from the dashboard API: each reply has the header `X-Carl-Version`, also a reply that says that the config did not change (304). The sync service saves it (`server_version` in its state file). Thus, `/carl` tells you about a CARL update on the server Mac at the next contact. Before the first contact, `/carl` uses the version in `remote.json` (from the package).
- `/carl` also shows every CARL piece on this computer with its state: the disk cache, the model check, the session switcher, the subagents sidebar, the coder, the browser, web search and LSP.

## The Clients tab

The Connect tab has two sub-tabs: **Setup** and **Clients**. Push `[` or `]` to change between them.

- **Clients** lists this Mac (its OpenCode and Pi configs) and each computer that syncs.
- For each computer: the name, when it was last seen (`● connected`, or `○` and how long ago), the user, how it syncs (`always`: the service; `at start`: a check at the start), its config against the config that the dashboard sent (`up to date`, `at next sync`, `on hold`), the system and the address. The full level adds the version of the config that each computer has.
- The dashboard keeps the list in `~/.config/carl/clients.json`. **[ Forget the computers not seen for a week ]** removes old rows. It does not ask first.

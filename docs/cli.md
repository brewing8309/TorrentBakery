# ⌨️ Command line

[← Documentation](README.md)

`tb` does everything the web UI does. On an installed system it runs as the service user with the service's settings (`TB_CONFIG`, `TB_OUT`), so both see the same profiles and releases. `tb <command> --help` shows every option.

## Bake
```sh
tb bake movie.mkv                                    # plain file
tb bake /media/share/Some.Folder                      # folder → one torrent
tb bake stash:1234 -t main -u pixhost.to              # Stash scene, tracker profile, image host
tb bake file.mp4 --stashbox https://stashdb.org/scenes/<id>
tb bake file.mp4 --cover poster.jpg                   # own cover instead of the animation
tb bake stash:1234 --cover-source stash --cover-format gif
tb bake stash:1234 -t hf -u pixhost.to --publish      # bake, then publish
```
| Option | |
|---|---|
| `-o, --out DIR` | output root (env `TB_OUT`, default `out`) |
| `-t, --tracker NAME` | tracker profile |
| `-u, --upload HOST` | image host: `pixhost.to`, `pixhost.cc`, `pixho.st`, `imgbox`, `imagebam`, `hamsterimg`, `imghost.dev`, `bitporn.eu` |
| `--sfw` | mark uploads family safe (default: adult) |
| `--cover FILE` | own image/GIF/WebP as cover |
| `--cover-source own\|stash` | build the animation, or use Stash's preview |
| `--cover-format webp\|gif` | cover format |
| `--stashbox URL` | stash-box scene for a plain file |
| `--publish / --no-publish` | upload to the tracker afterwards (default: profile's auto-publish) |

## Publish and seed
```sh
tb publish out/My_Release -t hf --dry-run             # fetch the form, show what would be sent
tb publish out/My_Release -t hf [--title …] [--category …]
tb seed out/My_Release -t hf                          # add to qBittorrent (again)
```

## Settings
```sh
tb tracker add NAME -a ANNOUNCE -s SOURCE [--public]
tb tracker set NAME --site TEMPLATE --cookie '…' --category …
tb tracker test NAME | tb tracker fetch-announce NAME | tb tracker list | tb tracker remove NAME

tb stash set URL --api-key KEY
tb stash box-add NAME https://stashdb.org/graphql --api-key KEY
tb stash map-add /stash/path /local/path

tb host list | tb host set HOST -o api_key=KEY
tb qbit set --url URL --username U --password P --path-map here=there | tb qbit test
tb tags show TAG | tb tags ignore TAG
tb auth status | tb auth set-password USER | tb auth disable
```

## Other
```sh
tb serve --host 0.0.0.0 --port 8080 --out out         # the web UI (the service does this for you)
tb version
```

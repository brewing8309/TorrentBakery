# Changelog

## 0.1.0 – unreleased
- Bake a release from a video file or a Stash scene: torrent, 5×7 screenshot sheet, main image, MediaInfo, BBCode forum post.
- Metadata from Stash and stash-box; image upload to pixhost (.to/.cc/pixho.st), imgbox, imagebam, hamsterimg.
- Animated cover (WebP/GIF) from 1.5-second clips spread over the video, or Stash's own preview.
- Web UI history that survives restarts; English interface with explained settings.
- Saved tracker profiles; CLI (`tb`) and web UI (`tb serve`).
- Publish to Gazelle trackers (Empornium, HappyFappy, FemdomCult, Enthralled) with a session cookie, add to qBittorrent, Stash→tracker tag mapping (default list from stash-empornium); `tb publish`, web UI publish section.
- Publish to UNIT3D trackers through their upload API (bitporn.eu): API token instead of a cookie, no announce URL needed.
- Review step between bake and upload in the web UI: category dropdowns with names everywhere, editable post, no upload without a chosen category.
- Image host imghost.dev (bitporn's own host) and a warning in the review step when a tracker cannot display the bake's image host.
- UNIT3D list cover and banner set through the web form with a browser session cookie (the API cannot), retry button on the result page.
- Installable from a release tarball (`install.sh`, systemd service, `uninstall.sh`).

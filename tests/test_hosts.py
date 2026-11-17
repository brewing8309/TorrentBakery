import json

import httpx
import pytest

from torrentbakery.config import Config
from torrentbakery.hosts import get_uploader
from torrentbakery.pipeline import bake
from torrentbakery.models import Release


@pytest.fixture
def img(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"\xff\xd8fake")
    return p


def test_pixhost(img):
    def h(req):
        assert str(req.url) == "https://api.pixhost.cc/images" and b'name="content_type"' in req.content
        return httpx.Response(200, json={"name": "a.jpg", "show_url": "https://pixhost.cc/show/8582/563_a.jpg", "th_url": "https://t1.pixhost.cc/thumbs/8582/563_a.jpg", "thumb_width": 1, "thumb_height": 1})
    u = get_uploader("pixhost.cc", Config(), httpx.MockTransport(h)).upload(img)
    assert u.direct_url == "https://img1.pixhost.cc/images/8582/563_a.jpg" and u.page_url.endswith("563_a.jpg")


def test_hamsterimg(img):
    def h(req):
        assert req.headers["X-API-Key"] == "k" and req.url.path == "/api/1/upload"
        return httpx.Response(200, json={"image": {"url": "https://i.example/a.jpg", "url_viewer": "https://v.example/a", "thumb": {"url": "https://i.example/a.th.jpg"}}})
    cfg = Config(hosts={"hamsterimg": {"api_key": "k"}})
    assert get_uploader("hamsterimg", cfg, httpx.MockTransport(h)).upload(img).direct_url == "https://i.example/a.jpg"
    with pytest.raises(ValueError):
        get_uploader("hamsterimg", Config())


def test_imgbox(img):
    def h(req):
        if req.url.path == "/":
            return httpx.Response(200, text='<meta content="CSRF" name="csrf-token" />')
        assert req.headers["X-CSRF-Token"] == "CSRF"
        if req.url.path == "/ajax/token/generate":
            return httpx.Response(200, json={"token_id": 1, "token_secret": "s"})
        assert req.url.path == "/upload/process" and b"token_secret" in req.content
        return httpx.Response(200, json={"files": [{"original_url": "https://images2.imgbox.com/x.jpg", "url": "https://imgbox.com/x", "thumbnail_url": "https://thumbs2.imgbox.com/x.jpg"}]})
    assert get_uploader("imgbox", Config(), httpx.MockTransport(h)).upload(img).page_url == "https://imgbox.com/x"


def test_imagebam(img):
    def h(req):
        p = req.url.path
        if p == "/":
            return httpx.Response(200, text='<meta name="csrf-token" content="CSRF">')
        if p == "/upload/session":
            return httpx.Response(200, json={"data": "tok", "session": "sess"})
        if p == "/upload":
            assert b'name="files[0]"' in req.content
            return httpx.Response(200, json={"success": "https://imagebam.com/upload/complete?session=sess"})
        assert p == "/upload/complete" and req.url.params["session"] == "sess"
        return httpx.Response(200, text='<input value="[URL=https://imagebam.com/view/ME1][IMG]https://thumbs4.imagebam.com/9b/46/00/ME1_t.jpg[/IMG][/URL]">')
    u = get_uploader("imagebam", Config(), httpx.MockTransport(h)).upload(img)
    assert u.direct_url == "https://images4.imagebam.com/9b/46/00/ME1.jpg" and u.page_url.endswith("/view/ME1")


def test_bake_with_uploader(video, tmp_path):
    class Fake:
        name = "fake"
        def upload(self, path, nsfw=True):
            from torrentbakery.hosts import Uploaded
            return Uploaded(f"https://host.example/{path.name}")
    out = bake(video, tmp_path, Config(), Release(title="T"), uploader=Fake())
    post = (out / "post.bbcode").read_text()
    assert "[img]https://host.example/contactsheet.jpg[/img]" in post and "[img]https://host.example/cover.jpg[/img]" in post


def test_unknown_host():
    with pytest.raises(KeyError):
        get_uploader("nope", Config())

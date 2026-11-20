import pytest

from torrentbakery.config import Config, Tracker
from torrentbakery.trackers import get_publisher, load_templates
from torrentbakery.trackers.template import TemplateError, parse

from test_publish import UPLOAD_PAGE, req, transport

COMMUNITY = """
schema = 1
id = "demo"
name = "Demo"
engine = "gazelle"
base_url = "https://demo.example/"
source = "DM"

[[auth.credentials]]
field = "cookie"
label = "Session cookie"

[gazelle]
field_ids = { tags = "tags" }
"""


def test_builtin_templates():
    tpls, errors = load_templates()
    assert errors == {} and set(tpls) >= {"happyfappy", "bitporn"}
    assert tpls["bitporn"].engine == "unit3d" and tpls["bitporn"].category_table[4] == "Amateur" and "bitporn.eu" in tpls["bitporn"].image_hosts
    assert tpls["happyfappy"].image_hosts == () and tpls["happyfappy"].source == "HF"


def test_community_template_is_loaded_and_used(tmp_path, monkeypatch):
    monkeypatch.setenv("TB_CONFIG", str(tmp_path / "config.toml"))
    d = tmp_path / "tracker-templates"
    d.mkdir()
    (d / "demo.toml").write_text(COMMUNITY)
    (d / "broken.toml").write_text('schema = 1\nid = "x"\nengine = "nope"\nname = "X"\n')
    tpls, errors = load_templates()
    assert tpls["demo"].origin == "community" and tpls["demo"].base_url == "https://demo.example" and "engine must be" in errors["broken.toml"]
    t = Tracker(["https://demo.example/PASS/announce"], "", True, site="demo", cookie="session=abc", category="2")
    assert t.effective_source == "DM" and t.effective_base_url == "https://demo.example"  # defaults from the template
    page = UPLOAD_PAGE.replace('id="taginput" name="taglist"', 'id="tags" name="tags"')
    posts = []
    get_publisher("d", Config(trackers={"d": t}), transport(posts, page)).publish(req(tmp_path))
    assert b'name="tags"' in posts[0].content  # [gazelle] field_ids applied


@pytest.mark.parametrize("patch,msg", [({"schema": 2}, "schema"), ({"id": "Bad Id"}, "id must"), ({"base_url": "ftp://x"}, "base_url"), ({"auth": {}}, "cookie")])
def test_template_validation(patch, msg):
    import tomllib
    data = {**tomllib.loads(COMMUNITY), **patch}
    with pytest.raises(TemplateError, match=msg):
        parse(data)

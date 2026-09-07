"""Тесты edtb_cf_panels / edtb_cf_panels_info (без EDT, только XML)."""
from edt_bridge.interface import edtb_cf_panels, edtb_cf_panels_info

MDO = "src/Configuration/Configuration.mdo"


def test_set_panel_location(project):
    res = edtb_cf_panels(
        {"panels": [{"panel": "NavigationPanel", "location": "Left"},
                    {"panel": "InformationPanel", "location": "Hidden"}]}
    )
    assert res["ok"], res
    info = edtb_cf_panels_info()
    assert info["panels"] == {"NavigationPanel": "Left", "InformationPanel": "Hidden"}


def test_invalid_location(project):
    res = edtb_cf_panels({"panels": [{"panel": "NavigationPanel", "location": "Center"}]})
    assert not res["ok"]
    assert "Center" in res["error"]


def test_home_page_replacement(project):
    res = edtb_cf_panels(
        {"homePage": {"columns": [
            {"forms": ["Catalog.Товары.Form.ListForm"]},
            {"forms": ["Document.Заказ.Form.ListForm", "CommonForm.Настройки"]},
        ]}}
    )
    assert res["ok"], res
    info = edtb_cf_panels_info()
    assert info["homePage"] == [
        ["Catalog.Товары.Form.ListForm"],
        ["Document.Заказ.Form.ListForm", "CommonForm.Настройки"],
    ]


def test_home_page_empty_columns_rejected(project):
    res = edtb_cf_panels({"homePage": {"columns": []}})
    assert not res["ok"]
    assert "columns" in res["error"]


def test_empty_ops_rejected(project):
    res = edtb_cf_panels({})
    assert not res["ok"]
    assert "Пустой набор" in res["error"]


def test_dry_run_no_write(project):
    before = (project / MDO).read_text(encoding="utf-8")
    res = edtb_cf_panels(
        {"panels": [{"panel": "NavigationPanel", "location": "Bottom"}]}, dryRun=True
    )
    assert res["ok"] and res["dryRun"]
    assert "Bottom" in res["diff"]
    assert (project / MDO).read_text(encoding="utf-8") == before


def test_mutations_forbidden(project, monkeypatch):
    monkeypatch.setenv("EDTB_ALLOW_FILE_MUTATIONS", "0")
    res = edtb_cf_panels({"panels": [{"panel": "NavigationPanel", "location": "Left"}]})
    assert not res["ok"]
    assert "EDTB_ALLOW_FILE_MUTATIONS" in res["error"]


def test_write_backup_and_resync_warning(project):
    res = edtb_cf_panels({"panels": [{"panel": "NavigationPanel", "location": "Right"}]})
    assert res["ok"]
    assert res["backup"]
    assert any("resync" in w or "EDT-MCP" in w for w in res["warnings"])


def test_info_fixture(project):
    info = edtb_cf_panels_info()
    assert info["ok"]
    assert info["panels"] == {"NavigationPanel": "Top"}
    assert info["homePage"] == [["CommonForm.РабочийСтол"]]

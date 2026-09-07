"""Тесты edtb_interface_edit / edtb_interface_info (без EDT, только XML)."""
import pytest

from edt_bridge.interface import edtb_interface_edit, edtb_interface_info

CMD = "Catalog.Товары.StandardCommand.OpenList"
CMD2 = "Document.Заказ.StandardCommand.OpenList"


def test_hide_adds_visibility_entry(project):
    res = edtb_interface_edit("Subsystem.Продажи", [{"operation": "hide", "command": CMD}])
    assert res["ok"], res
    info = edtb_interface_info("Subsystem.Продажи")
    assert info["commandsVisibility"][CMD] is False


def test_show_removes_visibility_entry(project):
    res = edtb_interface_edit(
        "Subsystem.Продажи",
        [{"operation": "show", "command": "Catalog.Товары.Command.СкрытаяКоманда"}],
    )
    assert res["ok"], res
    info = edtb_interface_info("Subsystem.Продажи")
    assert "Catalog.Товары.Command.СкрытаяКоманда" not in info["commandsVisibility"]


def test_show_not_hidden_warns(project):
    res = edtb_interface_edit("Subsystem.Продажи", [{"operation": "show", "command": CMD}])
    assert res["ok"]
    assert any("не была скрыта" in w for w in res["warnings"])


def test_place_and_order(project):
    res = edtb_interface_edit(
        "Subsystem.Продажи",
        [
            {"operation": "place", "command": CMD2, "group": "NavigationPanelImportant"},
            {"operation": "order", "command": CMD2, "group": "NavigationPanelImportant", "index": 5},
        ],
    )
    assert res["ok"], res
    info = edtb_interface_info("Subsystem.Продажи")
    assert info["placements"][CMD2] == "NavigationPanelImportant"
    assert {"command": CMD2, "group": "NavigationPanelImportant", "index": 5} in info["order"]
    # существующие записи сохранены
    assert info["placements"][CMD] == "NavigationPanelOrdinary"


def test_order_requires_index(project):
    res = edtb_interface_edit(
        "Subsystem.Продажи",
        [{"operation": "order", "command": CMD, "group": "NavigationPanelOrdinary"}],
    )
    assert not res["ok"]
    assert "index" in res["error"]


def test_unknown_operation(project):
    res = edtb_interface_edit("Subsystem.Продажи", [{"operation": "rename", "command": CMD}])
    assert not res["ok"]
    assert "rename" in res["error"]


def test_dry_run_returns_diff_and_does_not_write(project):
    before = (
        project / "src" / "Subsystems" / "Продажи" / "CommandInterface.xml"
    ).read_text(encoding="utf-8")
    res = edtb_interface_edit(
        "Subsystem.Продажи", [{"operation": "hide", "command": CMD}], dryRun=True
    )
    assert res["ok"] and res["dryRun"]
    assert "-</commandInterface>" not in res["diff"]
    assert "+<visibility>" in res["diff"] or "visibility" in res["diff"]
    after = (
        project / "src" / "Subsystems" / "Продажи" / "CommandInterface.xml"
    ).read_text(encoding="utf-8")
    assert before == after


def test_mutations_forbidden_by_default(project, monkeypatch):
    monkeypatch.setenv("EDTB_ALLOW_FILE_MUTATIONS", "0")
    res = edtb_interface_edit("Subsystem.Продажи", [{"operation": "hide", "command": CMD}])
    assert not res["ok"]
    assert "EDTB_ALLOW_FILE_MUTATIONS" in res["error"]


def test_write_creates_backup_and_resync_warning(project):
    res = edtb_interface_edit("Subsystem.Продажи", [{"operation": "hide", "command": CMD}])
    assert res["ok"]
    assert res["backup"]
    assert any("resync" in w or "EDT-MCP" in w for w in res["warnings"])


def test_bad_fqn(project):
    res = edtb_interface_edit("Catalog.Товары", [{"operation": "hide", "command": CMD}])
    assert not res["ok"]
    assert "FQN" in res["error"]


def test_missing_file(project):
    res = edtb_interface_info("Subsystem.НетТакой")
    assert not res["ok"]
    assert "не найден" in res["error"]


def test_info_reads_fixture(project):
    info = edtb_interface_info("Subsystem.Продажи")
    assert info["ok"]
    assert info["commandsVisibility"] == {"Catalog.Товары.Command.СкрытаяКоманда": False}
    assert info["placements"] == {CMD: "NavigationPanelOrdinary"}
    assert info["order"] == [{"command": CMD, "group": "NavigationPanelOrdinary", "index": 0}]

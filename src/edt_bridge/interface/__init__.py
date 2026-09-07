"""Пакет interface: командный интерфейс подсистем и панели конфигурации."""
from .interface import edtb_interface_edit, edtb_interface_info
from .panels import edtb_cf_panels, edtb_cf_panels_info

__all__ = [
    "edtb_interface_edit",
    "edtb_interface_info",
    "edtb_cf_panels",
    "edtb_cf_panels_info",
]

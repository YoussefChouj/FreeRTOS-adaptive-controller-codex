from pathlib import Path
from xml.etree import ElementTree


PROJECT = Path(__file__).parents[3] / "USER" / "JX_FLY.uvprojx"


def test_keil_flash_download_requests_reset_and_run():
    """The Download Function must release the core after verify.

    UL2CM3 stores the Program/Verify/Reset-and-Run selection in the ``-O14``
    operation mask. Without it, UV4 reports a successful download but leaves
    the MCU halted until a manual Debug/Run action.
    """
    root = ElementTree.parse(PROJECT).getroot()
    driver = root.findtext(".//FlashDriverDll") or ""
    assert driver.startswith("UL2CM3(-O14 ")

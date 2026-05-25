#
# Collect QIF file and lots as XLSX by running Quicken and automating its UI.
# pip install pywinauto
#
# For identifying controls use:
# python -m pywinauto.recorder

from pathlib import Path
from datetime import datetime
import time
import subprocess

from pywinauto import Application, Desktop
from pywinauto.keyboard import send_keys


QUICKEN_EXE = r"C:\Program Files (x86)\Quicken\qw.exe"
EXPORT_DIR = Path(r"C:\Users\preka\Quicken\QuickenExports")


def wait_window(title_re, timeout=30):
    return Desktop(backend="uia").window(title_re=title_re).wait(
        "visible ready", timeout=timeout
    )


def set_checkbox(win, title):
    cb = win.child_window(title=title, control_type="CheckBox")
    cb.wait("visible enabled", timeout=10)
    if cb.get_toggle_state() == 0:
        cb.toggle()


def main():
    today = datetime.now().strftime("%Y-%m-%d")

    qif_path = EXPORT_DIR / f"Quicken_{today}.QIF"
    lots_path = EXPORT_DIR / f"QuickenLots_{today}.xlsx"

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    for f in EXPORT_DIR.glob("Quicken*"):
        try:
            f.unlink()
        except IsADirectoryError:
            pass

    app = Application(backend="uia").start(QUICKEN_EXE)
    time.sleep(10)

    quicken = wait_window(r"Quicken Classic Premier.*")
    quicken.set_focus()

    # File -> Export -> QIF
    send_keys("%f")
    time.sleep(2)
    send_keys("e")
    time.sleep(2)
    send_keys("q")
    time.sleep(2)

    qif = wait_window(r"QIF Export", timeout=20)

    qif.child_window(
        title="QIF file to export to:",
        control_type="Edit"
    ).set_edit_text(str(qif_path))

    set_checkbox(qif, "Security list")
    set_checkbox(qif, "Category list")
    set_checkbox(qif, "Account list")

    # OK button may appear as Button or Pane in Quicken
    try:
        qif.child_window(title="OK", control_type="Button").click_input()
    except Exception:
        qif.child_window(title="OK").click_input()

    time.sleep(15)

    # Planning -> Portfolio
    quicken = wait_window(r"Quicken Classic Premier.*\[Home\].*", timeout=30)
    quicken.set_focus()

    quicken.child_window(title="Planning", control_type="MenuItem").click_input()
    time.sleep(1)

    Desktop(backend="uia").window(title_re=".*").child_window(
        title="Portfolio",
        control_type="MenuItem"
    ).click_input()

    time.sleep(3)

    investing = wait_window(r"Quicken Classic Premier.*\[Investing\].*", timeout=30)

    investing.child_window(title="Expand All").click_input()
    time.sleep(1)

    investing.child_window(title="Export").click_input()
    time.sleep(1)

    Desktop(backend="uia").window(title_re=".*").child_window(
        title="Export to Excel",
        control_type="MenuItem"
    ).click_input()

    save_as = wait_window(r".*(Save As|Export).*", timeout=20)

    save_as.child_window(
        title="File name:",
        control_type="Edit"
    ).set_edit_text(str(lots_path))

    save_as.child_window(title="Save", control_type="Button").click_input()

    time.sleep(2)

    investing = wait_window(r"Quicken Classic Premier.*\[Investing\].*", timeout=30)
    investing.child_window(title="Done").click_input()

    subprocess.run(
        ["taskkill", "/IM", "qw.exe", "/F"],
        capture_output=True,
        text=True
    )


if __name__ == "__main__":
    main()

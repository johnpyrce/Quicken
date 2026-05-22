'''
GUI Automation for Quicken to extract a QIF file of the current database
and the current lot definitions for all investment accounts as an XLSX file.

pip install pywinauto pyautogui
'''

from pathlib import Path
import time
import pyautogui
from pywinauto import Application, timings
from pywinauto.keyboard import send_keys

QUICKEN_EXE = r"C:\Program Files (x86)\Quicken\qw.exe"
QDF_FILE = r"C:\Users\preka\Quicken\YourFile.QDF"

EXPORT_DIR = Path(r"C:\Users\preka\Quicken\QuickenExports")
QIF_FILE = EXPORT_DIR / "all_accounts.qif"
PORTFOLIO_EXCEL_FILE = EXPORT_DIR / "portfolio.xlsx"

EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def wait(seconds=1):
    time.sleep(seconds)


def start_quicken():
    app = Application(backend="uia").start(f'"{QUICKEN_EXE}" "{QDF_FILE}"')
    wait(10)

    # Attach to main Quicken window
    app = Application(backend="uia").connect(title_re=".*Quicken.*")
    win = app.window(title_re=".*Quicken.*")
    win.wait("visible ready", timeout=60)
    win.set_focus()
    return app, win


def export_all_accounts_qif(win):
    """
    Uses keyboard/menu navigation.
    You may need to adjust keystrokes depending on your Quicken version.
    """

    win.set_focus()

    # File menu → Export → QIF File
    # Alt+F opens File menu.
    send_keys("%f")
    wait(0.5)

    # This may vary. If it fails, use Quicken's menu manually once
    # and replace this sequence with the correct letters.
    send_keys("e")
    wait(0.5)
    send_keys("q")
    wait(2)

    # QIF Export dialog
    dlg = win.child_window(title_re=".*QIF.*", control_type="Window")
    dlg.wait("visible ready", timeout=20)

    # Set QIF filename
    file_box = dlg.child_window(control_type="Edit", found_index=0)
    file_box.set_edit_text(str(QIF_FILE))

    # Select "All Accounts" if available
    try:
        accounts_combo = dlg.child_window(control_type="ComboBox", found_index=0)
        accounts_combo.select("All Accounts")
    except Exception:
        print("Could not select All Accounts automatically; continuing.")

    # Select common export checkboxes
    for label in [
        "Transactions",
        "Account List",
        "Category List",
        "Security Lists",
        "Memorized Payees",
    ]:
        try:
            cb = dlg.child_window(title_re=f".*{label}.*", control_type="CheckBox")
            if cb.get_toggle_state() == 0:
                cb.click_input()
        except Exception:
            pass

    # Click OK / Export
    for button_name in ["OK", "Export"]:
        try:
            dlg.child_window(title=button_name, control_type="Button").click_input()
            break
        except Exception:
            pass

    wait(5)

    # Handle overwrite confirmation if present
    try:
        confirm = win.child_window(title_re=".*Confirm.*|.*Quicken.*", control_type="Window")
        confirm.child_window(title_re="Yes|OK", control_type="Button").click_input()
    except Exception:
        pass

    print(f"QIF export attempted: {QIF_FILE}")


def go_to_investment_portfolio(win):
    win.set_focus()

    # Try menu path first: Investing → Portfolio
    # This may need adjustment.
    send_keys("%i")
    wait(0.5)
    send_keys("p")
    wait(3)


def expand_all_portfolio(win):
    win.set_focus()

    # Try to find a visible "Expand All" button
    try:
        btn = win.child_window(title_re=".*Expand All.*", control_type="Button")
        btn.wait("visible ready", timeout=10)
        btn.click_input()
        print("Clicked Expand All.")
        return
    except Exception:
        pass

    # Fallback: use image/coordinate style if button cannot be found.
    # You may need to replace this with a fixed coordinate from your screen.
    print("Could not find Expand All button by name.")
    print("Move mouse to Expand All button and record position with pyautogui.position().")


def export_portfolio_to_excel(win):
    win.set_focus()

    # Try to find Export button/menu
    try:
        btn = win.child_window(title_re=".*Export.*", control_type="Button")
        btn.wait("visible ready", timeout=10)
        btn.click_input()
        wait(1)
    except Exception:
        # Fallback: common menu shortcut attempts
        send_keys("%e")
        wait(1)

    # Depending on Quicken version, choose "Export to Excel" / "Export Data"
    try:
        send_keys("x")
        wait(2)
    except Exception:
        pass

    # Save As dialog
    try:
        save = win.child_window(title_re=".*Save.*|.*Export.*", control_type="Window")
        save.wait("visible ready", timeout=20)

        edit = save.child_window(control_type="Edit", found_index=0)
        edit.set_edit_text(str(PORTFOLIO_EXCEL_FILE))

        for button_name in ["Save", "OK", "Export"]:
            try:
                save.child_window(title=button_name, control_type="Button").click_input()
                break
            except Exception:
                pass

        print(f"Portfolio Excel export attempted: {PORTFOLIO_EXCEL_FILE}")

    except Exception as e:
        print("Could not complete Excel export automatically.")
        print(e)


def main():
    timings.Timings.window_find_timeout = 20

    app, win = start_quicken()

    export_all_accounts_qif(win)

    go_to_investment_portfolio(win)
    expand_all_portfolio(win)
    export_portfolio_to_excel(win)

    print("Done.")


if __name__ == "__main__":
    main()

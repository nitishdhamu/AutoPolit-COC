"""
Template Capture Tool — Interactive, Guided UI Element Capture
=============================================================
Uses only Pillow + tkinter (no OpenCV needed here).

Flow:
  1. Pick WHICH template you want to capture
  2. See a hint (which screenshot + what to draw)
  3. Pick the screenshot
  4. Draw a box in the window
  5. Confirm and save

Menu:
  1. Capture templates
  2. Verify templates
  3. Exit
"""

import sys
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageTk

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_ROOT   = Path(__file__).parent.parent
SCREENSHOT_DIR = PROJECT_ROOT / "Screenshots"
TEMPLATE_DIR   = PROJECT_ROOT / "templates"

# ── Template catalogue ─────────────────────────────────────────────────────────
# (subdirectory, template_name, description, hint, recommended_screenshot)
# hint       : what exactly to draw the box around
# rec_ss     : recommended screenshot filename (shown as suggestion, not forced)
_FULL_LEGACY_TEMPLATE_CATALOGUE = [
    # ── Screens ───────────────────────────────────────────────────────────────
    ("screens", "supercell_logo",
     "Supercell loading screen logo",
     "Capture just the Supercell logo/text in the centre — not the background.",
     "before loading bar screen.png"),

    ("screens", "loading_screen",
     "Loading progress bar",
     "Capture the loading bar at the bottom — not the CoC logo above or the percentage number.",
     "loding bar screen.png"),

    ("screens", "coc_logo",
     "CoC logo on the loading screen",
     "Capture the 'Clash of Clans' logo text at the top of the loading screen.",
     "loding bar screen.png"),

    ("screens", "home_base",
     "Home village — builder hut icon (top-left HUD)",
     "Capture only the builder hut icon in the top-left HUD — NOT the '2/5' numbers next to it.",
     "home base unzoomed.png"),

    ("screens", "bb_home_base",
     "Builder base — hammer icon (top-left HUD)",
     "Same as home_base but for builder base — capture only the hammer icon, not the numbers.",
     "builder base.png"),

    ("screens", "attack_results",
     "Attack result screen header (stars row)",
     "Capture the 3-star row at the top of the results screen. Include all 3 stars (even empty ones). Exclude loot numbers and buttons.",
     "after attack result screen.png"),

    ("screens", "scout_screen",
     "Scout/attack planning screen",
     "Capture the bottom troop bar showing your troops ready to deploy. This confirms the bot is in attack-prep mode.",
     "in attack screen.png"),

    ("screens", "battle_screen",
     "Active battle — troop deploy bar",
     "Capture the troop deploy bar at the very bottom of the screen during an active attack.",
     "in attack toops deployed .png"),

    ("screens", "supercell_id_panel",
     "SCID switcher popup panel",
     "Capture the entire white/beige SCID popup panel — include its title and close button. Exclude the game map behind it.",
     "coc acount switch menu button.png"),

    ("screens", "switch_id_accounts_panel",
     "Account list inside the SCID panel",
     "Capture the accounts list showing the 3 account entries.",
     "coc acounts menu.png"),

    ("screens", "builder_menu",
     "Home village — builder menu panel",
     "Capture the sliding builder panel — include the 'Builder' header + top of the upgrade list. Exclude the game map.",
     "home base builder menu.png"),

    ("screens", "lab_ui",
     "Home village — laboratory building menu",
     "Capture the lab panel header + upgrade button area. Exclude the game map behind it.",
     "home base laboratory menu.png"),

    ("screens", "hero_hall_ui",
     "Hero Hall building menu",
     "Capture the Hero Hall panel header and the top of the hero list.",
     "inside hero hall ui.png"),

    ("screens", "lab_research_ui",
     "Inside lab — research selection screen",
     "Capture the grid/list of upgradeable troops inside the lab.",
     "inside lab ui.png"),

    ("screens", "star_lab_ui",
     "Builder base — Star Laboratory menu",
     "Capture the Star Lab panel header + top of the research list.",
     "builder base laboratory menu.png"),

    ("screens", "bb_builder_menu",
     "Builder base — builder menu panel",
     "Capture the BB builder panel header + top of the upgrade list.",
     "builder base builder menu.png"),

    ("screens", "bb_troops_screen",
     "Builder base — troop training screen",
     "Capture the troop grid shown when training BB troops.",
     "builder base toops train screen.png"),

    ("screens", "army_screen",
     "Home village — army/troops menu",
     "Capture the troop icon grid. Exclude the resource bar at the top.",
     "troops menu.png"),

    ("screens", "super_troops_panel",
     "Super Troops selection/upgrade panel",
     "Capture the Super Troops panel title + top of the super troop entry.",
     "super dragon upgrade screen.png"),

    # ── Buttons ───────────────────────────────────────────────────────────────
    ("buttons", "attack_btn",
     "ATTACK button (bottom-left, home village)",
     "Capture just the red ATTACK button — button text + shape. Nothing around it.",
     "home base unzoomed.png"),

    ("buttons", "next_btn",
     "NEXT button during scouting",
     "Capture the green NEXT button at bottom-right during attack scouting.",
     "in attack screen.png"),

    ("buttons", "end_battle_btn",
     "END BATTLE button (top-left, during battle)",
     "Capture the small END BATTLE button at top-left of the active battle screen.",
     "in attack screen.png"),

    ("buttons", "return_home_btn",
     "RETURN HOME button on result screen",
     "Capture the RETURN HOME button at the bottom of the attack results screen.",
     "after attack result screen.png"),

    ("buttons", "surrender_btn",
     "SURRENDER button (during battle)",
     "Capture the SURRENDER button at top-left while troops are deployed.",
     "in attack toops deployed .png"),

    ("buttons", "settings_gear",
     "Settings gear icon (top-right corner)",
     "Capture just the gear icon — tight crop, top-right of screen. About 35x35 px.",
     "home base unzoomed.png"),

    ("buttons", "scid_open_btn",
     "Supercell ID open/switch button",
     "Capture the button that opens the account switcher (profile icon + SCID text).",
     "coc acount switch menu button.png"),

    ("buttons", "switch_id_btn",
     "SWITCH account button inside SCID panel",
     "Capture the SWITCH button inside the account list panel.",
     "coc acounts menu.png"),

    ("buttons", "bb_find_now_btn",
     "Builder base — FIND NOW / BATTLE button",
     "Capture the FIND NOW or BATTLE button on builder base attack screen.",
     "builder base attack button screen.png"),

    ("buttons", "bb_attack_btn",
     "ATTACK button on builder base",
     "Capture the ATTACK button on the builder base home screen (bottom-left).",
     "builder base.png"),

    ("buttons", "upgrade_confirm_btn",
     "UPGRADE confirm button in builder menu",
     "Capture just the green UPGRADE button. NOT the cost text above it.",
     "home base builder menu.png"),

    ("buttons", "train_btn",
     "TRAIN button (opens army menu)",
     "Capture the TRAIN button at the bottom-left of the home village screen.",
     "home base unzoomed.png"),

    ("buttons", "super_dragon_boost_btn",
     "Super Dragon BOOST confirmation button",
     "Capture the BOOST button on the Super Dragon payment confirmation screen.",
     "paying for super dragon.png"),

    ("buttons", "gpg_library_item",
     "Google Play Games — CoC library tile",
     "Capture the CoC game tile/card in the GPG library list (icon + title).",
     "google play games library.png"),

    ("buttons", "gpg_home_screen",
     "Google Play Games home screen header",
     "Capture the Google Play Games logo/header bar at the top.",
     "google playgames home.png"),

    ("buttons", "play_button",
     "Google Play Games — Play button for Clash of Clans",
     "Capture only the green PLAY button below the Clash of Clans title. Exclude the Update game button.",
     "google play games coc window with update button.png"),

    # ── Icons ─────────────────────────────────────────────────────────────────
    ("icons", "gold_icon",
     "Gold resource icon (top-left HUD)",
     "Capture only the gold coin icon — NOT the number next to it. About 30x30 px.",
     "home base unzoomed.png"),

    ("icons", "elixir_icon",
     "Elixir resource icon (top-left HUD)",
     "Capture only the purple elixir drop icon — NOT the number. About 30x30 px.",
     "home base unzoomed.png"),

    ("icons", "de_icon",
     "Dark Elixir icon (top-left HUD)",
     "Capture only the dark elixir drop icon — NOT the number. About 30x30 px.",
     "home base unzoomed.png"),

    ("icons", "gem_icon",
     "Gem icon (top-left HUD)",
     "Capture only the green gem icon — NOT the number. About 30x30 px.",
     "home base unzoomed.png"),

    ("icons", "builder_icon",
     "Builder hammer icon (shows free/total count)",
     "Capture only the hammer icon — NOT the '2/5' numbers. Bot reads numbers via OCR separately.",
     "home base unzoomed.png"),

    # ── Troops ────────────────────────────────────────────────────────────────
    ("troops", "super_dragon_icon",
     "Super Dragon troop icon in training menu",
     "Capture just the Super Dragon portrait icon in the training list — not the count below.",
     "super dragoin train menu.png"),

    ("troops", "earthquake_icon",
     "Earthquake spell icon in spell menu",
     "Capture just the Earthquake spell icon in the spell training list.",
     "speel train menu.png"),

    ("troops", "super_dragon_menu_icon",
     "Super Dragon icon in the barrel/super troops popup",
     "Capture the larger Super Dragon icon shown in the activation popup — different from the training one.",
     "barrel opened for super toops.png"),

    ("troops", "super_troops_tab",
     "Super Troops tab button",
     "Capture the 'Super Troops' tab button at the top of the troops panel.",
     "super dragon upgrade screen.png"),

    # ── Dialogs ───────────────────────────────────────────────────────────────
    ("dialogs", "gem_purchase_dialog",
     "Gem purchase popup (not enough resources)",
     "Capture the FULL gem purchase dialog box — title, cost, and buttons all included.",
     "not enough resources, use gem.png"),

    ("dialogs", "gem_purchase_x_btn",
     "X button to close the gem purchase popup",
     "Capture just the X close button at the top-right of the gem dialog. Tight crop.",
     "not enough resources, use gem.png"),

    ("dialogs", "gem_cost_btn",
     "Gem cost button inside the purchase popup",
     "Capture the blue gem cost button (shows the gem amount to spend).",
     "not enough resources, use gem.png"),

    ("dialogs", "dialog_close_x",
     "Generic close X button",
     "Capture the X button — same as gem_purchase_x_btn, used as a generic dialog closer.",
     "not enough resources, use gem.png"),

    ("dialogs", "reload_game_btn",
     "OK/Reload button on connection-lost error",
     "Capture just the OK or RELOAD button inside the connection error popup.",
     "When coc losses its connection or the account is opened in my phon, so it shows thi serror, so it should reload the game, if it keep comming just reload and try to switch account as i am playing on this acc.png"),

    ("dialogs", "connection_lost",
     "Connection lost / open on phone dialog",
     "Capture the FULL error dialog — title + message + button. Bot uses this to detect when it needs to reload.",
     "When coc losses its connection or the account is opened in my phon, so it shows thi serror, so it should reload the game, if it keep comming just reload and try to switch account as i am playing on this acc.png"),

    ("dialogs", "update_btn",
     "Update button in Google Play Games",
     "Capture the UPDATE button shown in GPG when CoC has a pending update.",
     "google play games coc window with update button.png"),
]

# OCR handles textual controls.  Keep only icon-only, loading, and safety
# templates in the active capture workflow.  The legacy catalogue above is
# retained as reference material for future UI changes, but is never shown.
TEMPLATE_CATALOGUE = [
    ("buttons", "play_button", "GPG Play button", "Tight crop of the green Play button for Clash of Clans.", "google play games coc window with update button.png"),
    ("dialogs", "update_btn", "GPG Update game button", "Tight crop of the Update game button.", "google play games coc window with update button.png"),
    ("screens", "loading_screen", "CoC loading progress bar", "Crop the progress bar only.", "loding bar screen.png"),
    ("screens", "home_base", "Home Village HUD marker", "Crop the builder/hammer HUD icon, excluding numbers.", "home base unzoomed.png"),
    ("screens", "bb_home_base", "Builder Base HUD marker", "Crop its distinct builder HUD icon, excluding numbers.", "builder base.png"),
    ("icons", "gem_icon", "Gem icon", "Tight crop of only the green gem icon.", "home base unzoomed.png"),
    ("dialogs", "gem_purchase_dialog", "Gem purchase dialog", "Crop the whole central gem-purchase dialog.", "not enough resources, use gem.png"),
    ("dialogs", "gem_purchase_x_btn", "Gem dialog close X", "Tight crop of the red X on the gem dialog.", "not enough resources, use gem.png"),
    ("dialogs", "dialog_close_x", "Generic close X", "Tight crop of a red dialog close X.", "not enough resources, use gem.png"),
    ("dialogs", "reload_game_btn", "Connection recovery button", "Crop only Reload/OK in the connection error dialog.", "When coc losses its connection or the account is opened in my phon, so it shows thi serror, so it should reload and try to switch account as i am playing on this acc.png"),
    ("dialogs", "connection_lost", "Connection-lost dialog", "Crop the whole connection-lost dialog.", "When coc losses its connection or the account is opened in my phon, so it shows thi serror, so it should reload and try to switch account as i am playing on this acc.png"),
    ("icons", "builder_icon", "Builder HUD icon", "Tight crop of the builder icon; OCR reads its count.", "home base unzoomed.png"),
    ("troops", "super_dragon_icon", "Super Dragon icon", "Crop the troop portrait only.", "super dragoin train menu.png"),
    ("troops", "earthquake_spell_icon", "Earthquake spell icon", "Crop the spell icon only.", "speel train menu.png"),
    # ── Pet House ─────────────────────────────────────────────────────────────
    ('screens', 'pet_house_ui', 'Pet House building menu', 'Capture the Pet House panel header and the top of the pet list.', 'inside hero hall ui.png'),
    ('buttons', 'pet_house', 'Pet House building icon', 'Capture just the Pet House building on the home village. Tight crop.', 'home base unzoomed.png'),
    ('buttons', 'pet_upgrade_btn', 'Pet upgrade confirm button', 'Capture the green Upgrade button inside the pet upgrade dialog.', 'inside hero hall ui.png'),
    # ── Hero Hall ─────────────────────────────────────────────────────────────
    ('screens', 'hero_hall_ui', 'Hero Hall building menu', 'Capture the Hero Hall panel header and the top of the hero list.', 'inside hero hall ui.png'),
    ('buttons', 'hero_hall', 'Hero Hall building icon', 'Capture just the Hero Hall building on the home village. Tight crop.', 'home base unzoomed.png'),
    ('buttons', 'hero_upgrade_btn', 'Hero upgrade confirm button', 'Capture the green Upgrade button inside the hero upgrade dialog.', 'inside hero hall ui.png'),
    # ── Misc referenced templates ─────────────────────────────────────────────
    ('icons', 'gold_icon', 'Gold resource icon', 'Crop only the gold coin icon, not the number. ~30x30px.', 'home base unzoomed.png'),
    ('icons', 'elixir_icon', 'Elixir resource icon', 'Crop only the purple elixir drop icon, not the number. ~30x30px.', 'home base unzoomed.png'),
    ('icons', 'de_icon', 'Dark Elixir icon', 'Crop only the dark elixir drop icon, not the number. ~30x30px.', 'home base unzoomed.png'),
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def clear():
    print("\033[2J\033[H", end="")


def header(title: str):
    print()
    print("═" * 60)
    print(f"  {title}")
    print("═" * 60)


def ensure_template_dirs():
    for subdir, *_ in TEMPLATE_CATALOGUE:
        (TEMPLATE_DIR / subdir).mkdir(parents=True, exist_ok=True)


def get_screenshots() -> list[Path]:
    if not SCREENSHOT_DIR.exists():
        return []
    files = sorted(SCREENSHOT_DIR.glob("*.png")) + sorted(SCREENSHOT_DIR.glob("*.jpg"))
    return [f for f in files if f.name.lower() != "desktop.ini"]


def tpl_path(subdir: str, name: str) -> Path:
    return TEMPLATE_DIR / subdir / f"{name}.png"


def tpl_exists(subdir: str, name: str) -> bool:
    return tpl_path(subdir, name).exists()


def load_pil(path: Path) -> Image.Image | None:
    try:
        return Image.open(path).convert("RGB")
    except Exception as e:
        print(f"\n  ❌ Could not open: {path.name}  ({e})")
        input("  Press Enter to go back…")
        return None


# ── Tkinter ROI Selector ───────────────────────────────────────────────────────

class ROISelector:
    MAX_W = 1280
    MAX_H =  720

    def __init__(self, pil_img: Image.Image, source_name: str):
        self.pil_img     = pil_img
        self.source_name = source_name
        self.orig_w, self.orig_h = pil_img.size

        scale_x = self.MAX_W / self.orig_w
        scale_y = self.MAX_H / self.orig_h
        self.scale = min(scale_x, scale_y, 1.0)

        self.disp_w = int(self.orig_w * self.scale)
        self.disp_h = int(self.orig_h * self.scale)

        self.result: tuple[int, int, int, int] | None = None
        self._sx = self._sy = 0
        self._rect_id = None

    def run(self) -> tuple[int, int, int, int] | None:
        self.root = tk.Tk()
        self.root.title(
            f"Draw a box  ·  ENTER = confirm  ·  ESC = cancel  ·  {self.source_name}"
        )
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a1a")

        disp_img = self.pil_img.resize((self.disp_w, self.disp_h), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(disp_img)

        self.canvas = tk.Canvas(
            self.root, width=self.disp_w, height=self.disp_h,
            cursor="crosshair", highlightthickness=0, bg="#000000",
        )
        self.canvas.pack()
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)

        self._status_var = tk.StringVar(
            value="  Click and drag to draw a rectangle  |  ENTER to confirm  |  ESC to cancel"
        )
        tk.Label(
            self.root, textvariable=self._status_var,
            font=("Consolas", 10), bg="#1a1a1a", fg="#aaaaaa",
            anchor="w", padx=8, pady=4,
        ).pack(fill=tk.X)

        self.canvas.bind("<ButtonPress-1>",   self._press)
        self.canvas.bind("<B1-Motion>",       self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.root.bind("<Return>",   self._confirm)
        self.root.bind("<KP_Enter>", self._confirm)
        self.root.bind("<Escape>",   self._cancel)
        self.root.protocol("WM_DELETE_WINDOW", self._cancel)

        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw - self.disp_w)//2}+{(sh - self.disp_h - 60)//2}")
        self.root.lift()
        self.root.focus_force()
        self.root.mainloop()
        return self.result

    def _press(self, e):
        self._sx, self._sy = e.x, e.y
        if self._rect_id:
            self.canvas.delete(self._rect_id)
            self._rect_id = None

    def _drag(self, e):
        if self._rect_id:
            self.canvas.delete(self._rect_id)
        self._rect_id = self.canvas.create_rectangle(
            self._sx, self._sy, e.x, e.y,
            outline="#00ff88", width=2, dash=(5, 3),
        )
        rw = int(abs(e.x - self._sx) / self.scale)
        rh = int(abs(e.y - self._sy) / self.scale)
        self._status_var.set(
            f"  {rw} × {rh} px  —  release mouse, then press ENTER to confirm"
        )

    def _release(self, e):
        if self._rect_id:
            self.canvas.delete(self._rect_id)
        self._rect_id = self.canvas.create_rectangle(
            self._sx, self._sy, e.x, e.y,
            outline="#00ff88", width=2,
        )
        rw = int(abs(e.x - self._sx) / self.scale)
        rh = int(abs(e.y - self._sy) / self.scale)
        self._status_var.set(
            f"  Selection: {rw} × {rh} px  —  ENTER to confirm  or  drag again to redo"
        )

    def _confirm(self, _=None):
        if not self._rect_id:
            self._status_var.set("  ⚠  Draw a rectangle first!")
            return
        coords = self.canvas.coords(self._rect_id)
        if not coords:
            return
        x1d, y1d, x2d, y2d = coords
        x = int(min(x1d, x2d) / self.scale)
        y = int(min(y1d, y2d) / self.scale)
        w = int(abs(x2d - x1d) / self.scale)
        h = int(abs(y2d - y1d) / self.scale)
        if w < 4 or h < 4:
            self._status_var.set("  ⚠  Selection too small — drag a bigger box!")
            return
        x = max(0, min(x, self.orig_w - 1))
        y = max(0, min(y, self.orig_h - 1))
        w = max(1, min(w, self.orig_w - x))
        h = max(1, min(h, self.orig_h - y))
        self.result = (x, y, w, h)
        self.root.destroy()

    def _cancel(self, _=None):
        self.result = None
        self.root.destroy()


# ── Preview window ─────────────────────────────────────────────────────────────

def show_preview(crop: Image.Image):
    cw, ch = crop.size
    min_side = 180
    scale = max(min_side / cw, min_side / ch, 1.0)
    pw, ph = int(cw * scale), int(ch * scale)
    disp = crop.resize((pw, ph), Image.NEAREST)

    root = tk.Tk()
    root.title(f"Preview  [{cw}×{ch} px]  —  auto-closes in 4 s")
    root.resizable(False, False)
    root.configure(bg="#1a1a1a")
    photo = ImageTk.PhotoImage(disp)
    tk.Label(root, image=photo, bd=0, bg="#1a1a1a").pack()
    tk.Label(root, text=f"Captured region: {cw} × {ch} px",
             font=("Consolas", 10), bg="#1a1a1a", fg="#888888", pady=4).pack(fill=tk.X)

    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"+{(sw - pw)//2}+{(sh - ph)//2}")
    root.lift()
    root.after(4000, root.destroy)
    root.mainloop()


# ── Capture flow ───────────────────────────────────────────────────────────────

def run_interactive_capture():
    clear()
    header("CAPTURE TEMPLATES  —  Interactive Mode")

    screenshots = get_screenshots()
    if not screenshots:
        print(f"\n  ❌ No screenshots found in:\n     {SCREENSHOT_DIR}")
        print("\n  Put your CoC game screenshots in that folder and try again.")
        input("\n  Press Enter to go back…")
        return

    ensure_template_dirs()

    while True:
        # ── Step 1: Pick which template to capture ──────────────────────────
        chosen = _pick_template()
        if chosen is None:
            return  # back to main menu

        subdir, name, description, hint, rec_ss = chosen

        # ── Step 2: Show hint ───────────────────────────────────────────────
        clear()
        header(f"CAPTURING:  {name}")
        print(f"\n  Category    : {subdir}")
        print(f"  Description : {description}")
        print()
        print(f"  📌 What to draw a box around:")
        # Wrap hint at 55 chars
        words = hint.split()
        line, lines = "", []
        for w in words:
            if len(line) + len(w) + 1 > 55:
                lines.append(line)
                line = w
            else:
                line = (line + " " + w).strip()
        if line:
            lines.append(line)
        for l in lines:
            print(f"     {l}")
        print()
        print(f"  📂 Suggested screenshot: {rec_ss}")
        already = tpl_exists(subdir, name)
        if already:
            print(f"\n  ⚠  Already captured — you can overwrite it.")
        print()

        # ── Step 3: Pick screenshot ─────────────────────────────────────────
        ss_path = _pick_screenshot(screenshots, suggested=rec_ss)
        if ss_path is None:
            continue  # went back — pick another template

        pil_img = load_pil(ss_path)
        if pil_img is None:
            continue

        # ── Step 4: Draw box ────────────────────────────────────────────────
        print()
        print("  Opening selector window…")
        print("  Draw a box around the element, then press ENTER to confirm.")

        roi = ROISelector(pil_img, ss_path.name).run()

        if roi is None:
            print("\n  Cancelled — nothing saved.")
            input("  Press Enter to continue…")
            continue

        x, y, w, h = roi
        crop = pil_img.crop((x, y, x + w, y + h))

        # ── Step 5: Preview + save ──────────────────────────────────────────
        show_preview(crop)

        clear()
        header("SAVE TEMPLATE")
        print(f"\n  Template    : {subdir}/{name}")
        print(f"  From        : {ss_path.name}")
        print(f"  Coordinates : x={x}  y={y}  w={w}  h={h}")
        print()

        out = tpl_path(subdir, name)
        if out.exists():
            print(f"  ⚠  Already exists. Overwrite? (y/n): ", end="")
            if input().strip().lower() != "y":
                print("  Not saved.")
                input("  Press Enter to continue…")
                continue

        crop.save(str(out), format="PNG")
        print(f"\n  ✅  Saved  →  templates/{subdir}/{name}.png  ({w}×{h} px)")

        print()
        print("  Capture another template? (y/n): ", end="")
        if input().strip().lower() != "y":
            return


def _pick_template() -> tuple | None:
    """Show full catalogue, user picks what they want to capture. Returns tuple or None."""
    clear()
    header("WHICH TEMPLATE DO YOU WANT TO CAPTURE?")
    print()

    current_subdir = None
    index_map: dict[int, tuple] = {}
    counter = 1

    for entry in TEMPLATE_CATALOGUE:
        subdir, name, description, hint, rec_ss = entry
        if subdir != current_subdir:
            current_subdir = subdir
            print(f"  ── {subdir.upper()} " + "─" * (44 - len(subdir)))
        done = "✅" if tpl_exists(subdir, name) else "  "
        print(f"  {done} {counter:2}.  {name:<35}  {description}")
        index_map[counter] = entry
        counter += 1

    print()
    print("  [0]  Back to main menu")
    print()

    while True:
        raw = input("  Enter number: ").strip()
        if raw == "0":
            return None
        if raw.isdigit() and int(raw) in index_map:
            return index_map[int(raw)]
        print("  ⚠  Invalid. Enter a number from the list.")


def _pick_screenshot(screenshots: list[Path], suggested: str = "") -> Path | None:
    """Show screenshot list with suggested one highlighted. Returns Path or None."""
    clear()
    header("PICK A SCREENSHOT")

    if suggested:
        print(f"\n  💡 Suggested: {suggested}\n")

    for i, ss in enumerate(screenshots, 1):
        size_kb = ss.stat().st_size // 1024
        tag = "  ← suggested" if ss.name == suggested else ""
        print(f"  {i:2}.  {ss.name}  ({size_kb:,} KB){tag}")

    print()
    print("  [0]  Back (pick a different template)")
    print()

    while True:
        raw = input("  Enter number: ").strip()
        if raw == "0":
            return None
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(screenshots):
                return screenshots[idx]
        print("  ⚠  Invalid. Try again.")


# ── Verify Templates ───────────────────────────────────────────────────────────

def run_verify_templates():
    clear()
    header("TEMPLATE STATUS")
    print()
    ensure_template_dirs()

    done_count, missing_list = 0, []
    current_subdir = None

    for subdir, name, description, *_ in TEMPLATE_CATALOGUE:
        if subdir != current_subdir:
            current_subdir = subdir
            print(f"  ── {subdir.upper()} " + "─" * (44 - len(subdir)))

        path = tpl_path(subdir, name)
        if path.exists():
            try:
                img = Image.open(path)
                w, h = img.size
                print(f"  ✅  {name:<35}  {w}×{h} px")
                done_count += 1
            except Exception:
                print(f"  ❌  {name:<35}  (corrupted!)")
                missing_list.append(f"{subdir}/{name}")
        else:
            print(f"  ○   {name:<35}  missing")
            missing_list.append(f"{subdir}/{name}")

    total = len(TEMPLATE_CATALOGUE)
    print()
    print("─" * 60)
    print(f"  Done    : {done_count} / {total}")
    print(f"  Missing : {len(missing_list)}")
    if missing_list:
        print()
        for m in missing_list:
            print(f"    •  {m}")
    print()
    input("  Press Enter to go back…")


# ── Main Menu ──────────────────────────────────────────────────────────────────

def main():
    while True:
        clear()
        header("COC BOT — Template Capture Tool")
        print()
        print("  Capture the UI elements the bot needs to recognise.")
        print()
        print("  [1]  Capture templates  (guided — pick template, then screenshot)")
        print("  [2]  Verify templates   (see which are done / missing)")
        print("  [3]  Exit")
        print()

        choice = input("  Choice: ").strip()

        if choice == "1":
            run_interactive_capture()
        elif choice == "2":
            run_verify_templates()
        elif choice == "3" or choice.lower() in ("q", "exit"):
            clear()
            print("\n  Goodbye!\n")
            sys.exit(0)
        else:
            print("  ⚠  Press 1, 2, or 3.")
            input("  Press Enter…")


if __name__ == "__main__":
    main()

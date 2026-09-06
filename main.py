"""
Calendar App - Home / Assessments / Classes tabs (v12 - live-ticking clock)
------------------------------------------------------------------
Canvas dimensions: 595 x 1024 (portrait, mobile-app style)

v12 changes (from v11):
- The header clock (added in v11) now visibly ticks every second
  instead of only updating on the 30s full-page redraw cycle. A
  separate self.root.after(1000, self._tick_clock) loop, started once
  from __init__ right after the first redraw(), just itemconfigures
  the two existing clock text items in place - it doesn't touch the
  rest of the canvas. If a full redraw happens to land between ticks
  (delete("all") invalidates the old item ids), the tick is skipped
  with a caught tk.TclError, since the full redraw already drew a
  fresh, correct clock anyway.

v11 changes (from v10):
- The current date and time are now shown in the top-right corner of
  the header, next to the tabs (_draw_header_clock, called from
  draw_tabs). It uses the same `now` (ics_data.now(), which picks up
  TEST_TIME_OFFSET) that the rest of redraw() already computes, so it
  updates on the normal REDRAW_INTERVAL_MS cycle along with everything
  else - no separate timer needed - and honors the same test-time
  override as the rest of the app.

v10 changes (from v9):
- Added a dark mode, toggled with the "4" key (alongside the existing
  1/2/3 tab-switch keys - see TAB_KEY_BINDINGS and _on_key_press).
  All colors now live in two dicts, LIGHT_THEME and DARK_THEME, instead
  of loose module constants; CalendarShapeApp keeps the active one on
  self.theme and every drawing method reads its colors from there
  (self.theme["TEXT_PRIMARY"], etc.) instead of a bare name, so
  _toggle_dark_mode just swaps self.theme, updates the canvas's own
  background, and redraws - no other state changes.

v9 changes (from v8):
- Assessment card titles (on both the Home "Next due" card and the
  Assessments tab list) now wrap onto multiple lines instead of
  running off the right edge of the card. Wrapping is measured with
  tkinter.font so it accounts for the real rendered width of the
  title text, not just a character count. Each assessment card's
  height now grows to fit however many lines its title needs
  (_assessment_card_height), and the course label / bottom padding
  shift down to match, so short one-line titles look exactly as they
  did before (same 100px card height) while long titles get the
  extra room they need instead of being clipped.

v8 changes (from v7):
- Tabs now switch via the keyboard instead of clicking on the canvas.
  TAB_KEY_BINDINGS (a global dict) maps each key to a tab -
  "1" -> Home, "2" -> Assessments, "3" -> Classes. The app binds
  <Key> to _on_key_press via bind_all (not bind), so a keystroke
  switches tabs no matter which widget currently has focus - a plain
  root.bind() missed keystrokes whenever the canvas had grabbed focus,
  which is what made it feel finicky. The handler also matches on
  event.keysym rather than event.char, since keysym stays "1"/"2"/"3"
  regardless of Shift, Caps Lock, or keyboard layout.

v7 changes (from v6):
- Added the Assessments and Classes tabs.
    - Home: unchanged - "Next due" card + today's classes below it.
    - Assessments: every upcoming assessment, stacked as cards that
      look identical to the home tab's "Next due" card (shared via the
      new _draw_assessment_card helper).
    - Classes: the same class-block view as the home tab, but starting
      right below the tab bar so it gets the whole page instead of only
      the space left under the "Next due" card.
- draw_classes() now takes an explicit start_y parameter instead of
  reading self.classes_start_y, so both the home tab and the Classes
  tab can reuse it with their own starting position.

v6 changes (from v5):
- The last class of the day no longer lingers after it ends - it now
  disappears the moment it's over, instead of hanging around for
  CLASS_LINGER_MINUTES like classes earlier in the day do (those still
  linger until 10 minutes into the next class, so you don't lose sight
  of a class the instant it ends while there's another one coming up).

v5 changes (from v4):
- The app now gets "now" from ics_data.now() instead of calling
  datetime.now(TZ) directly, so it picks up TEST_TIME_OFFSET
  (see ics_data.py) - set that to `timedelta(weeks=2)` (or whatever
  you like) to test the classes list / current-time line as if you
  were 2 weeks in the future, without touching your system clock.
- The "NEXT DUE" assessment pill now always shows the date, not just
  the time for timed assessments (e.g. "Due Sep 20, 2:30 PM" instead
  of just "Due 2:30 PM"). All-day assessments still show just the date.

v4 changes (from v3, still in effect):
- Real data now comes from ics_data.ICSStore, which downloads the
  Canvas assessments feed and the class timetable feed, caches them
  to ./data/*.ics, and refreshes them from the network once an hour
  on a background thread (see ics_data.py for details).
- The UI redraws itself every 30 seconds (REDRAW_INTERVAL_MS) so the
  current-time line, which class is "current", and which class has
  aged out of view all stay accurate between the hourly data refreshes.
- "Today's classes" are filtered to the local NZ calendar day.
- A class stays visible until 10 minutes after the START of the next
  class in today's list (or 10 minutes after its own end, if it's the
  last class of the day) - this is the "disappears 10 minutes into the
  next class" rule from the original spec.
- Empty states: "No assessments due" / "No classes today" render when
  a feed has nothing upcoming, instead of leaving a blank card.

Requires: pip install Pillow requests icalendar tzdata --break-system-packages
("tzdata" supplies the IANA timezone database, which Windows lacks.)

v3 changes (from v2, still in effect):
- Tkinter's Canvas has no anti-aliasing, so create_arc-based rounded
  corners looked jagged/stair-stepped, especially on the small tab
  radius (r=10) where a second inset shape was drawn just 1-2px
  inside the first to fake a border.
- Every rounded-rect/rounded-corner shape is now rendered offscreen
  with Pillow at 4x resolution and downsampled with LANCZOS
  (supersampling), producing smooth anti-aliased corners, then
  placed on the canvas as an image. Text stays as native Tkinter
  canvas text (already smooth via the OS font renderer).
"""

import tkinter as tk
import tkinter.font as tkfont
import functools
from datetime import timedelta
from PIL import Image, ImageDraw, ImageTk

from ics_data import ICSStore, TZ, now as get_now

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # older Pillow versions
    RESAMPLE = Image.LANCZOS

REDRAW_INTERVAL_MS = 30_000  # how often the UI repaints itself
CLASS_LINGER_MINUTES = 10    # a class stays visible this long into the next one

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
WIDTH = 600
HEIGHT = 1024

TAB_BAR_AREA_HEIGHT = 70        # total header area reserved for tabs
TAB_HEIGHT_ACTIVE = 46
TAB_HEIGHT_INACTIVE = 40
TAB_H_PADDING = 22               # horizontal text padding inside each tab
TAB_GAP = 5                      # small gap between tabs, no overlap
TAB_LEFT_MARGIN = 18             # tabs start near the left edge

PIXELS_PER_HOUR = 88             # scales class block height by duration
MARGIN = 20
CARD_RADIUS = 14
PAGE_BOTTOM = HEIGHT - MARGIN     # content that would extend past here is
                                  # skipped entirely rather than drawn cut off

DUE_CARD_BASE_HEIGHT = 100        # height for a single-line title
DUE_CARD_TITLE_FONT = ("Helvetica", 15, "bold")
DUE_CARD_COURSE_FONT = ("Helvetica", 11)
DUE_CARD_TITLE_LINE_HEIGHT = 20   # vertical space each extra title line adds

# ---------------------------------------------------------------------
# THEMES (light / dark)
# ---------------------------------------------------------------------
# All colors live in these two dicts instead of loose module constants,
# so the whole UI can switch palettes at runtime (see CalendarShapeApp's
# self.theme / _toggle_dark_mode, bound to the "4" key). Every drawing
# method reads colors from self.theme[...] rather than a bare name.
LIGHT_THEME = {
    "BG_COLOR": "#F4F5F9",

    # Tabs — small paper/file-folder style, left-aligned
    "TAB_FILL": "#FFFFFF",
    "TAB_BORDER_ACTIVE": "#2E6FE0",
    "TAB_BORDER_INACTIVE": "#B9CCEC",
    "TAB_TEXT_ACTIVE": "#2E6FE0",
    "TAB_TEXT_INACTIVE": "#9AA6BF",
    "TAB_SHADOW": "#E2E6F0",
    "PAGE_BG": "#FFFFFF",   # the "paper" body below the tabs

    # Assessment card
    "DUE_CARD_BG": "#FFFFFF",
    "DUE_CARD_ACCENT": "#FF9F43",       # left accent strip + badge color
    "DUE_CARD_ACCENT_BG": "#FFF3E3",
    "DUE_CARD_SHADOW": "#E3E5EE",

    # Section headers
    "SECTION_LABEL_COLOR": "#9AA0B4",
    "DIVIDER_COLOR": "#EAECF2",

    # Class blocks
    "CLASS_BLOCK_BG": "#FFFFFF",
    "CLASS_BLOCK_BORDER": "#E4E7F0",
    "CLASS_ACCENT": "#4A6CF7",

    "CLASS_CURRENT_BG": "#FFFFFF",
    "CLASS_CURRENT_ACCENT": "#2ECC71",
    "CLASS_CURRENT_GLOW": "#E9FBF0",

    "CLASS_PAST_BG": "#F7F7F9",
    "CLASS_PAST_ACCENT": "#C7CAD6",
    "CLASS_PAST_TEXT": "#B0B3C0",

    "CLASS_SHADOW": "#E9EBF2",

    "TIME_LINE_COLOR": "#FF4757",

    "TEXT_PRIMARY": "#1D2233",
    "TEXT_SECONDARY": "#7B7F91",
}

DARK_THEME = {
    "BG_COLOR": "#14161F",

    # Tabs
    "TAB_FILL": "#1E212E",
    "TAB_BORDER_ACTIVE": "#5B8DEF",
    "TAB_BORDER_INACTIVE": "#333850",
    "TAB_TEXT_ACTIVE": "#8FB4FF",
    "TAB_TEXT_INACTIVE": "#6B7189",
    "TAB_SHADOW": "#0B0C12",
    "PAGE_BG": "#1B1E29",

    # Assessment card
    "DUE_CARD_BG": "#232735",
    "DUE_CARD_ACCENT": "#FFB05E",
    "DUE_CARD_ACCENT_BG": "#3A2E1E",
    "DUE_CARD_SHADOW": "#0C0D12",

    # Section headers
    "SECTION_LABEL_COLOR": "#6B7189",
    "DIVIDER_COLOR": "#2B2F3D",

    # Class blocks
    "CLASS_BLOCK_BG": "#232735",
    "CLASS_BLOCK_BORDER": "#31384A",
    "CLASS_ACCENT": "#6C8CFF",

    "CLASS_CURRENT_BG": "#232735",
    "CLASS_CURRENT_ACCENT": "#3EDC84",
    "CLASS_CURRENT_GLOW": "#17301F",

    "CLASS_PAST_BG": "#1A1C26",
    "CLASS_PAST_ACCENT": "#3A3F4F",
    "CLASS_PAST_TEXT": "#54596B",

    "CLASS_SHADOW": "#0C0D12",

    "TIME_LINE_COLOR": "#FF6B7A",

    "TEXT_PRIMARY": "#EDEFF7",
    "TEXT_SECONDARY": "#9CA0B4",
}

THEMES = {"light": LIGHT_THEME, "dark": DARK_THEME}


# ---------------------------------------------------------------------
# TAB STATE
# ---------------------------------------------------------------------
# Global identifiers for each tab, plus the state that tracks which one
# is currently active, and which keyboard key switches to each one.
TAB_HOME = "Home"
TAB_ASSESSMENTS = "Assessments"
TAB_CLASSES = "Classes"
TAB_LABELS = [TAB_HOME, TAB_ASSESSMENTS, TAB_CLASSES]

active_tab = TAB_HOME    # which tab is currently selected

# Which key needs to be pressed to jump to each tab.
TAB_KEY_BINDINGS = {
    "1": TAB_HOME,
    "2": TAB_ASSESSMENTS,
    "3": TAB_CLASSES,
}


# ---------------------------------------------------------------------
# DATA HELPERS
# ---------------------------------------------------------------------
def format_assessment_time(assessment):
    """Always shows the date; adds the time on top for timed assessments.
    e.g. 'Due Sep 20, 2:30 PM' or 'Due Sep 20' for all-day items."""
    due = assessment["due"]
    date_part = due.strftime("%b %d")
    if assessment["has_time"]:
        time_part = due.strftime("%I:%M %p").lstrip("0")
        return f"Due {date_part}, {time_part}"
    return f"Due {date_part}"


def compute_visible_classes(classes, now):
    """A class stays visible until CLASS_LINGER_MINUTES after the next
    class starts. The last class of the day gets no linger - it
    disappears the moment it ends."""
    visible = []
    n = len(classes)
    for i, cls in enumerate(classes):
        if i + 1 < n:
            disappears_at = classes[i + 1]["start"] + timedelta(minutes=CLASS_LINGER_MINUTES)
        else:
            disappears_at = cls["end"]
        if now < disappears_at:
            visible.append(cls)
    return visible


def class_state(cls, now):
    if now < cls["start"]:
        return "upcoming"
    if cls["start"] <= now <= cls["end"]:
        return "current"
    return "past"  # ended, but still lingering in the visible window


# ---------------------------------------------------------------------
# ANTI-ALIASED ROUNDED-RECT RENDERING (Pillow, supersampled)
# ---------------------------------------------------------------------
def _corner_bbox_and_angles(corner, w, h, r):
    """PIL angle convention: 0=right, 90=down, 180=left, 270=up,
    sweeping clockwise. Returns (bbox, start_angle, end_angle)."""
    if corner == "tl":
        return (0, 0, 2 * r, 2 * r), 180, 270
    if corner == "tr":
        return (w - 2 * r, 0, w, 2 * r), 270, 360
    if corner == "bl":
        return (0, h - 2 * r, 2 * r, h), 90, 180
    if corner == "br":
        return (w - 2 * r, h - 2 * r, w, h), 0, 90
    raise ValueError(corner)


@functools.lru_cache(maxsize=512)
def render_rounded_rect(w, h, r, fill=None, outline=None, outline_width=1,
                         corners=("tl", "tr", "bl", "br"), supersample=4):
    """Render a rectangle with the requested corners rounded, anti-aliased
    via supersampling, and return a PIL Image (RGBA) at the target size.

    Wrapped with lru_cache below: most shapes we draw (a card's shadow,
    body, accent strip, or the "!" badge) have the exact same
    width/height/fill on every single card, every redraw - only things
    like the due-date pill vary in width. Without caching, every one of
    those identical shapes gets fully re-rendered (supersampled + resized)
    from scratch on every redraw, which is what made a longer list (e.g.
    the Assessments tab with many entries) noticeably slow to draw."""
    w = max(int(round(w)), 1)
    h = max(int(round(h)), 1)
    # radius can never exceed half the shape's width/height, or the
    # corner arcs overlap past the centre and the geometry goes invalid
    r = max(min(r, w / 2, h / 2), 0)
    ss = supersample
    W, H, R = w * ss, h * ss, int(round(r * ss))
    ow = max(int(round(outline_width * ss)), 1) if outline else 0

    big = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)

    if fill:
        d.rectangle([0, R, W, H - R], fill=fill)
        d.rectangle([R, 0, W - R, H], fill=fill)
        for c in ("tl", "tr", "bl", "br"):
            if c in corners:
                bbox, a0, a1 = _corner_bbox_and_angles(c, W, H, R)
                d.pieslice(bbox, a0, a1, fill=fill)
            else:
                # square this corner off (rounded rect defaults to square
                # unless a corner is explicitly requested as rounded)
                if c == "tl":
                    d.rectangle([0, 0, R, R], fill=fill)
                elif c == "tr":
                    d.rectangle([W - R, 0, W, R], fill=fill)
                elif c == "bl":
                    d.rectangle([0, H - R, R, H], fill=fill)
                elif c == "br":
                    d.rectangle([W - R, H - R, W, H], fill=fill)

    if outline:
        left_top = R if "tl" in corners else 0
        left_bot = R if "bl" in corners else 0
        right_top = R if "tr" in corners else 0
        right_bot = R if "br" in corners else 0
        top_left_x = R if "tl" in corners else 0
        top_right_x = W - R if "tr" in corners else W
        bot_left_x = R if "bl" in corners else 0
        bot_right_x = W - R if "br" in corners else W

        d.line([0, left_top, 0, H - left_bot], fill=outline, width=ow)
        d.line([W - 1, right_top, W - 1, H - right_bot], fill=outline, width=ow)
        d.line([top_left_x, 0, top_right_x, 0], fill=outline, width=ow)
        d.line([bot_left_x, H - 1, bot_right_x, H - 1], fill=outline, width=ow)
        for c in corners:
            bbox, a0, a1 = _corner_bbox_and_angles(c, W, H, R)
            d.arc(bbox, a0, a1, fill=outline, width=ow)

    return big.resize((w, h), RESAMPLE)


# ---------------------------------------------------------------------
# APP
# ---------------------------------------------------------------------
class CalendarShapeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Calendar App - Home (Shape Mockup v3)")
        self.root.geometry(f"{WIDTH}x{HEIGHT}")
        self.root.attributes("-fullscreen", True)
        self.root.wait_visibility(self.root)
        self.root.wm_attributes("-type", "splash")
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.focus_force()

        # Dark mode toggles via the "4" key (see _on_key_press). The
        # active palette lives in self.theme and every drawing method
        # reads its colors from there, so toggling just swaps the dict
        # and redraws - no other state changes.
        self.dark_mode = False
        self.theme = THEMES["light"]

        self.canvas = tk.Canvas(
            root, width=WIDTH, height=HEIGHT, bg=self.theme["BG_COLOR"],
            highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)

        # Tabs switch via keyboard (1/2/3 -> Home/Assessments/Classes,
        # see TAB_KEY_BINDINGS). bind_all (not bind) so the key still
        # works no matter which widget currently has focus - the canvas
        # can silently grab focus after a click, which made a plain
        # root.bind() miss keystrokes.
        self.root.bind_all("<Key>", self._on_key_press)
        self.root.focus_set()

        # Keep references to every PhotoImage we create, or Tkinter will
        # garbage-collect them and the shapes will vanish.
        self._images = []

        self.store = ICSStore()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.redraw()
        # Kick off the header clock's own 1-second ticker (see
        # _tick_clock) - separate from the 30s full-page redraw, so the
        # displayed time visibly moves every second. Started after the
        # first redraw() so the clock text items it updates already
        # exist.
        self.root.after(1000, self._tick_clock)

    def _on_close(self):
        self.store.stop()
        self.root.destroy()

    def _on_key_press(self, event):
        """Switch tabs via the keyboard, per TAB_KEY_BINDINGS (1/2/3), or
        toggle dark mode with "4". Uses event.keysym rather than
        event.char - keysym stays "1"/"2"/"3"/"4" no matter what Shift,
        Caps Lock, or the keyboard layout do to the character produced,
        which is what made this feel finicky before."""
        global active_tab
        if event.keysym == "4":
            self._toggle_dark_mode()
            return
        label = TAB_KEY_BINDINGS.get(event.keysym)
        if label and label != active_tab:
            active_tab = label
            self.redraw()

    def _toggle_dark_mode(self):
        """Flip between the light and dark palettes and repaint. The
        canvas's own bg is updated directly (not just redrawn shapes),
        since anything not covered by a filled rectangle - e.g. a sliver
        at the very edge - would otherwise still show the old color."""
        self.dark_mode = not self.dark_mode
        self.theme = THEMES["dark"] if self.dark_mode else THEMES["light"]
        self.canvas.configure(bg=self.theme["BG_COLOR"])
        self.redraw()

    def redraw(self):
        """Clear the canvas and repaint everything with the latest data
        and the current time. Scheduled to repeat every REDRAW_INTERVAL_MS
        so the current-time line and class visibility stay accurate
        between the hourly data refreshes."""
        self.canvas.delete("all")
        self._images = []

        now = get_now()  # picks up ics_data.TEST_TIME_OFFSET when set

        self.draw_tabs(now)
        self.draw_page_body()

        if active_tab == TAB_HOME:
            next_assessment = self.store.get_next_assessment()
            todays_classes = self.store.get_todays_classes()
            visible_classes = compute_visible_classes(todays_classes, now)
            self.draw_next_assessment_card(next_assessment)
            self.draw_section_divider()
            self.draw_classes(visible_classes, now, self.classes_start_y)
        elif active_tab == TAB_ASSESSMENTS:
            assessments = self.store.get_upcoming_assessments()
            self.draw_assessments_tab(assessments)
        elif active_tab == TAB_CLASSES:
            todays_classes = self.store.get_todays_classes()
            visible_classes = compute_visible_classes(todays_classes, now)
            self.draw_classes_tab(visible_classes, now)

        self.root.after(REDRAW_INTERVAL_MS, self.redraw)

    # -------------------------------------------------------------
    # Shared helper: render a rounded rect via Pillow and place it
    # on the canvas at (x0, y0).
    # -------------------------------------------------------------
    def draw_rounded(self, x0, y0, x1, y1, r=14, fill="", outline="", width=1,
                      corners=("tl", "tr", "bl", "br")):
        img = render_rounded_rect(
            x1 - x0, y1 - y0, r,
            fill=fill or None, outline=outline or None, outline_width=width,
            corners=corners,
        )
        tkimg = ImageTk.PhotoImage(img)
        self._images.append(tkimg)
        self.canvas.create_image(x0, y0, anchor="nw", image=tkimg)
        return tkimg

    # -------------------------------------------------------------
    # TOP TABS — small paper file-folder tabs, left-aligned, each
    # sized to its own label rather than stretched across the page
    # -------------------------------------------------------------
    def draw_tabs(self, now):
        tab_labels = TAB_LABELS
        theme = self.theme

        # background behind the tabs
        self.canvas.create_rectangle(
            0, 0, WIDTH, TAB_BAR_AREA_HEIGHT, fill=theme["BG_COLOR"], outline=""
        )

        self._draw_header_clock(now)

        baseline_y = TAB_BAR_AREA_HEIGHT  # every tab's flat bottom sits here
        x = TAB_LEFT_MARGIN

        for label in tab_labels:
            is_active = (label == active_tab)
            h = TAB_HEIGHT_ACTIVE if is_active else TAB_HEIGHT_INACTIVE
            font = ("Helvetica", 14, "bold") if is_active else ("Helvetica", 13)
            text_color = theme["TAB_TEXT_ACTIVE"] if is_active else theme["TAB_TEXT_INACTIVE"]
            border_color = theme["TAB_BORDER_ACTIVE"] if is_active else theme["TAB_BORDER_INACTIVE"]
            border_w = 2 if is_active else 1

            # size the tab to its text, like a real folder label
            text_w = self._measure_text(label, font)
            tab_w = text_w + TAB_H_PADDING * 2

            x0 = x
            x1 = x + tab_w
            y1 = baseline_y
            y0 = y1 - h

            # soft shadow
            self.draw_rounded(
                x0 + 2, y0 + 2, x1 + 2, y1 + 2,
                r=10, fill=theme["TAB_SHADOW"], corners=("tl", "tr")
            )
            # tab body + border (a larger colored shape behind a slightly
            # inset white shape, to fake a clean border)
            self.draw_rounded(
                x0, y0, x1, y1, r=10,
                fill=border_color, corners=("tl", "tr")
            )
            inset = border_w
            self.draw_rounded(
                x0 + inset, y0 + inset, x1 - inset, y1,
                r=max(10 - inset, 2), fill=theme["TAB_FILL"], corners=("tl", "tr")
            )

            self.canvas.create_text(
                (x0 + x1) / 2, y0 + (y1 - y0) / 2 + 2,
                text=label, fill=text_color, font=font
            )

            # next tab starts after a small gap, no overlap
            x = x1 + TAB_GAP

    def _draw_header_clock(self, now):
        """Current date + time, right-aligned in the tab bar header next
        to the tabs. Drawn fresh on every full redraw (so it's correct
        right after a tab switch or a dark-mode toggle), and also kept
        ticking every second in between by _tick_clock, which just
        updates these two items' text in place rather than waiting on
        the next full REDRAW_INTERVAL_MS repaint."""
        theme = self.theme
        date_text = now.strftime("%a %d %b")             # e.g. "Sun 06 Sep"
        time_text = now.strftime("%I:%M %p").lstrip("0")  # e.g. "6:45 PM"

        x = WIDTH - MARGIN
        mid_y = TAB_BAR_AREA_HEIGHT / 2
        self._clock_date_id = self.canvas.create_text(
            x, mid_y - 10,
            text=date_text, anchor="e",
            fill=theme["TEXT_SECONDARY"], font=("Helvetica", 10, "bold")
        )
        self._clock_time_id = self.canvas.create_text(
            x, mid_y + 10,
            text=time_text, anchor="e",
            fill=theme["TEXT_PRIMARY"], font=("Helvetica", 14, "bold")
        )

    def _tick_clock(self):
        """Per-second clock update, independent of the full
        REDRAW_INTERVAL_MS repaint. Only updates the two header clock
        text items in place (itemconfigure) instead of clearing and
        redrawing the whole canvas, so the displayed time actually
        ticks over every second instead of only refreshing every 30s
        along with the rest of the page."""
        now = get_now()
        date_text = now.strftime("%a %d %b")
        time_text = now.strftime("%I:%M %p").lstrip("0")
        try:
            self.canvas.itemconfigure(self._clock_date_id, text=date_text)
            self.canvas.itemconfigure(self._clock_time_id, text=time_text)
        except tk.TclError:
            # A full redraw cleared the canvas between ticks (delete("all")
            # invalidates old item ids) - the next full redraw already
            # draws a fresh, correct clock, so just skip this tick.
            pass
        self.root.after(1000, self._tick_clock)

    def _measure_text(self, text, font):
        """Estimate rendered text width using a throwaway canvas item."""
        temp_id = self.canvas.create_text(0, 0, text=text, font=font)
        bbox = self.canvas.bbox(temp_id)
        self.canvas.delete(temp_id)
        return (bbox[2] - bbox[0]) if bbox else len(text) * 7

    # -------------------------------------------------------------
    # PAGE BODY — the "paper" surface below/behind the active tab
    # -------------------------------------------------------------
    def draw_page_body(self):
        self.canvas.create_rectangle(
            0, TAB_BAR_AREA_HEIGHT, WIDTH, HEIGHT,
            fill=self.theme["PAGE_BG"], outline=""
        )
        self.body_top = TAB_BAR_AREA_HEIGHT

    # -------------------------------------------------------------
    # ASSESSMENT CARD TEXT WRAPPING
    # -------------------------------------------------------------
    def _title_max_width(self, x0, x1):
        """Available horizontal space for the title text, matching the
        badge/text_x layout used in _draw_assessment_card (badge starts
        at x0+26, is 34px wide, with 16px padding before the text)."""
        text_x = x0 + 26 + 34 + 16
        return max(x1 - 18 - text_x, 40)

    def _wrap_text_lines(self, text, font, max_width):
        """Greedily wrap text into lines that fit max_width, measured
        with the real font metrics (tkinter.font) rather than a rough
        character count, so wrapping lines up with what's actually
        rendered on the canvas."""
        fnt = tkfont.Font(font=font)
        if fnt.measure(text) <= max_width:
            return [text]
        words = text.split()
        lines = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if not current or fnt.measure(candidate) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    def _assessment_card_height(self, assessment, x0, x1):
        """How tall this assessment's card needs to be so its title
        (wrapped to fit) doesn't get clipped. A single-line title keeps
        the original DUE_CARD_BASE_HEIGHT; each extra wrapped line adds
        DUE_CARD_TITLE_LINE_HEIGHT."""
        max_w = self._title_max_width(x0, x1)
        lines = self._wrap_text_lines(assessment["name"], DUE_CARD_TITLE_FONT, max_w)
        extra_lines = max(len(lines) - 1, 0)
        return DUE_CARD_BASE_HEIGHT + extra_lines * DUE_CARD_TITLE_LINE_HEIGHT

    # -------------------------------------------------------------
    # NEXT DUE ASSESSMENT CARD (home tab)
    # -------------------------------------------------------------
    def draw_next_assessment_card(self, assessment):
        theme = self.theme
        section_y = self.body_top + MARGIN
        self.canvas.create_text(
            MARGIN, section_y,
            text="NEXT DUE", anchor="w",
            fill=theme["SECTION_LABEL_COLOR"],
            font=("Helvetica", 10, "bold")
        )

        card_y0 = section_y + 22
        x0 = MARGIN
        x1 = WIDTH - MARGIN
        card_h = (
            self._assessment_card_height(assessment, x0, x1)
            if assessment is not None else DUE_CARD_BASE_HEIGHT
        )
        card_y1 = card_y0 + card_h

        if card_y1 > PAGE_BOTTOM:
            # Doesn't fully fit on the page - skip it rather than draw a
            # card that gets cut off at the bottom edge.
            self.after_due_card_y = card_y0
            return

        if assessment is None:
            # soft drop shadow + empty card body
            self.draw_rounded(
                x0 + 3, card_y0 + 4, x1 + 3, card_y1 + 4,
                r=CARD_RADIUS, fill=theme["DUE_CARD_SHADOW"]
            )
            self.draw_rounded(
                x0, card_y0, x1, card_y1,
                r=CARD_RADIUS, fill=theme["DUE_CARD_BG"]
            )
            self.canvas.create_text(
                (x0 + x1) / 2, (card_y0 + card_y1) / 2,
                text="No assessments due", fill=theme["TEXT_SECONDARY"],
                font=("Helvetica", 12)
            )
        else:
            self._draw_assessment_card(assessment, x0, card_y0, x1, card_y1)

        self.after_due_card_y = card_y1

    # -------------------------------------------------------------
    # Shared assessment-card renderer: shadow, body, accent strip,
    # "!" badge, name, course, and the due-date pill. Used by both the
    # home tab's "Next due" card and the Assessments tab's list, so
    # every assessment block looks identical no matter where it's shown.
    # -------------------------------------------------------------
    def _draw_assessment_card(self, assessment, x0, card_y0, x1, card_y1):
        theme = self.theme
        # soft drop shadow
        self.draw_rounded(
            x0 + 3, card_y0 + 4, x1 + 3, card_y1 + 4,
            r=CARD_RADIUS, fill=theme["DUE_CARD_SHADOW"]
        )
        # card body
        self.draw_rounded(
            x0, card_y0, x1, card_y1,
            r=CARD_RADIUS, fill=theme["DUE_CARD_BG"]
        )
        # left accent strip
        self.draw_rounded(
            x0, card_y0, x0 + 8, card_y1,
            r=4, fill=theme["DUE_CARD_ACCENT"]
        )

        # small badge (icon placeholder) top-left of text area
        badge_x0, badge_y0 = x0 + 26, card_y0 + 18
        badge_size = 34
        self.draw_rounded(
            badge_x0, badge_y0,
            badge_x0 + badge_size, badge_y0 + badge_size,
            r=10, fill=theme["DUE_CARD_ACCENT_BG"]
        )
        self.canvas.create_text(
            badge_x0 + badge_size / 2, badge_y0 + badge_size / 2,
            text="!", fill=theme["DUE_CARD_ACCENT"],
            font=("Helvetica", 16, "bold")
        )

        text_x = badge_x0 + badge_size + 16

        # Title: wrap to fit the available width instead of running off
        # the edge of the card. A single-line title lands at exactly the
        # same spot as before (card_y0 + 26); extra lines are centered
        # around that same anchor point so the badge still lines up
        # roughly with the middle of the title block.
        max_w = self._title_max_width(x0, x1)
        lines = self._wrap_text_lines(assessment["name"], DUE_CARD_TITLE_FONT, max_w)
        title_top_y = card_y0 + 26 - ((len(lines) - 1) * DUE_CARD_TITLE_LINE_HEIGHT) / 2
        for i, line in enumerate(lines):
            self.canvas.create_text(
                text_x, title_top_y + i * DUE_CARD_TITLE_LINE_HEIGHT,
                text=line, anchor="w",
                fill=theme["TEXT_PRIMARY"], font=DUE_CARD_TITLE_FONT
            )

        # Course label sits just below the wrapped title block, shifting
        # down as the title grows from one line to several.
        course_y = card_y0 + 26 + ((len(lines) - 1) * DUE_CARD_TITLE_LINE_HEIGHT) + 24
        self.canvas.create_text(
            text_x, course_y,
            text=assessment["course"], anchor="w",
            fill=theme["TEXT_SECONDARY"], font=DUE_CARD_COURSE_FONT
        )

        # time pill, bottom-right of card — always includes the date
        pill_text = format_assessment_time(assessment)
        pill_w = 16 + len(pill_text) * 6.5
        pill_x1 = x1 - 18
        pill_x0 = pill_x1 - pill_w
        pill_y0 = card_y1 - 34
        pill_y1 = card_y1 - 14
        self.draw_rounded(
            pill_x0, pill_y0, pill_x1, pill_y1,
            r=10, fill=theme["DUE_CARD_ACCENT_BG"]
        )
        self.canvas.create_text(
            (pill_x0 + pill_x1) / 2, (pill_y0 + pill_y1) / 2,
            text=pill_text, fill=theme["DUE_CARD_ACCENT"],
            font=("Helvetica", 10, "bold")
        )

    # -------------------------------------------------------------
    # ASSESSMENTS TAB — every upcoming assessment, stacked as cards
    # identical in style to the home tab's "Next due" card.
    # -------------------------------------------------------------
    def draw_assessments_tab(self, assessments):
        theme = self.theme
        x0 = MARGIN
        x1 = WIDTH - MARGIN
        y = self.body_top + MARGIN

        self.canvas.create_text(
            MARGIN, y,
            text="UPCOMING ASSESSMENTS", anchor="w",
            fill=theme["SECTION_LABEL_COLOR"],
            font=("Helvetica", 10, "bold")
        )
        y += 22

        if not assessments:
            self.canvas.create_text(
                (x0 + x1) / 2, y + 40,
                text="No assessments due", fill=theme["TEXT_SECONDARY"],
                font=("Helvetica", 12)
            )
            return

        for assessment in assessments:
            card_h = self._assessment_card_height(assessment, x0, x1)
            card_y0 = y
            card_y1 = card_y0 + card_h
            if card_y1 > PAGE_BOTTOM:
                # Doesn't fully fit on the page - stop here instead of
                # drawing a card that gets cut off at the bottom edge.
                # Assessments are in date order, and cards only get
                # taller/stack downward, so nothing after this would fit
                # either.
                break
            self._draw_assessment_card(assessment, x0, card_y0, x1, card_y1)
            y = card_y1 + 14

    # -------------------------------------------------------------
    # SECTION DIVIDER between assessment card and classes list
    # -------------------------------------------------------------
    def draw_section_divider(self):
        theme = self.theme
        y = self.after_due_card_y + MARGIN
        self.canvas.create_line(
            MARGIN, y, WIDTH - MARGIN, y, fill=theme["DIVIDER_COLOR"], width=1
        )
        y += 22
        self.canvas.create_text(
            MARGIN, y,
            text="TODAY'S CLASSES", anchor="w",
            fill=theme["SECTION_LABEL_COLOR"],
            font=("Helvetica", 10, "bold")
        )
        self.classes_start_y = y + 22

    # -------------------------------------------------------------
    # CLASSES TAB — same class blocks as the home tab, but spanning
    # the whole page below the tabs instead of sharing space with the
    # "Next due" card.
    # -------------------------------------------------------------
    def draw_classes_tab(self, classes, now):
        self.canvas.create_text(
            MARGIN, self.body_top + MARGIN,
            text="TODAY'S CLASSES", anchor="w",
            fill=self.theme["SECTION_LABEL_COLOR"],
            font=("Helvetica", 10, "bold")
        )
        self.draw_classes(classes, now, self.body_top + MARGIN + 22)

    # -------------------------------------------------------------
    # CLASS BLOCKS (height scales with duration)
    # -------------------------------------------------------------
    def draw_classes(self, classes, now, start_y):
        theme = self.theme
        x0 = MARGIN
        x1 = WIDTH - MARGIN
        y = start_y

        if not classes:
            self.canvas.create_text(
                (x0 + x1) / 2, y + 20,
                text="No classes today", fill=theme["TEXT_SECONDARY"],
                font=("Helvetica", 12)
            )
            return

        # First pass: work out where each card lands vertically, before
        # drawing anything. We need every card's y0/y1 up front so that,
        # if "now" doesn't fall inside any class, we can still place the
        # red time indicator in the right gap (or before the first class)
        # instead of only ever drawing it inside a "current" card.
        layouts = []
        cursor = y
        for cls in classes:
            duration_hours = (cls["end"] - cls["start"]).total_seconds() / 3600
            block_height = max(duration_hours * PIXELS_PER_HOUR, 54)
            y0 = cursor
            y1 = cursor + block_height
            if y1 > PAGE_BOTTOM:
                # Doesn't fully fit on the page - stop laying out further
                # classes instead of drawing one that gets cut off at the
                # bottom edge. Classes are in start-time order and blocks
                # only stack downward, so nothing after this would fit
                # either.
                break
            layouts.append((cls, y0, y1))
            cursor = y1 + 14  # gap between class blocks

        if not layouts:
            return

        for cls, y0, y1 in layouts:
            block_height = y1 - y0
            state = class_state(cls, now)
            if state == "current":
                bg, accent = theme["CLASS_CURRENT_BG"], theme["CLASS_CURRENT_ACCENT"]
                title_color, sub_color = theme["TEXT_PRIMARY"], theme["TEXT_SECONDARY"]
                # subtle glow behind current block
                self.draw_rounded(
                    x0 - 4, y0 - 4, x1 + 4, y1 + 4,
                    r=CARD_RADIUS + 4, fill=theme["CLASS_CURRENT_GLOW"]
                )
            elif state == "past":
                bg, accent = theme["CLASS_PAST_BG"], theme["CLASS_PAST_ACCENT"]
                title_color, sub_color = theme["CLASS_PAST_TEXT"], theme["CLASS_PAST_TEXT"]
            else:
                bg, accent = theme["CLASS_BLOCK_BG"], theme["CLASS_ACCENT"]
                title_color, sub_color = theme["TEXT_PRIMARY"], theme["TEXT_SECONDARY"]

            # drop shadow
            self.draw_rounded(
                x0 + 2, y0 + 3, x1 + 2, y1 + 3,
                r=CARD_RADIUS, fill=theme["CLASS_SHADOW"]
            )
            # card body
            self.draw_rounded(
                x0, y0, x1, y1,
                r=CARD_RADIUS, fill=bg,
                outline=theme["CLASS_BLOCK_BORDER"] if state != "current" else "",
                width=1,
            )
            # left accent strip
            self.draw_rounded(
                x0, y0, x0 + 7, y1,
                r=4, fill=accent
            )

            text_x = x0 + 24
            title = cls["course"] + (f" · {cls['activity']}" if cls["activity"] else "")
            time_label = f"{cls['start'].strftime('%H:%M')} - {cls['end'].strftime('%H:%M')}"

            # Center the title/location pair vertically in the card so
            # the top and bottom padding stay even no matter how tall
            # the block is (short classes vs. long labs/tutorials).
            center_y = (y0 + y1) / 2
            title_y = center_y - 10
            location_y = center_y + 10

            self.canvas.create_text(
                text_x, title_y,
                text=title, anchor="w",
                fill=title_color, font=("Helvetica", 12, "bold")
            )
            self.canvas.create_text(
                text_x, location_y,
                text=cls["location"], anchor="w",
                fill=sub_color, font=("Helvetica", 10)
            )
            self.canvas.create_text(
                x1 - 16, title_y,
                text=time_label, anchor="e",
                fill=sub_color, font=("Helvetica", 10, "bold")
            )

            # current-time line, positioned by actual elapsed fraction
            if state == "current":
                elapsed = (now - cls["start"]).total_seconds()
                total = (cls["end"] - cls["start"]).total_seconds()
                fraction = max(0.0, min(1.0, elapsed / total)) if total > 0 else 0.0
                line_y = y0 + block_height * fraction
                self._draw_time_indicator(x0, x1, line_y)

        # Second pass: if "now" isn't inside any class (no card drew the
        # indicator above), decide whether it belongs before the first
        # class, in a gap between two classes, or nowhere at all:
        #   - before the first class starts today -> show it just above
        #     the first card
        #   - in a gap between two classes -> show it centered in that
        #     gap
        #   - after the last class has ended -> no line, classes are over
        was_current = any(class_state(cls, now) == "current" for cls, _, _ in layouts)
        if not was_current:
            first_cls, first_y0, _ = layouts[0]
            last_cls, _, last_y1 = layouts[-1]
            if now < first_cls["start"]:
                self._draw_time_indicator(x0, x1, first_y0)
            elif now > last_cls["end"]:
                pass  # classes are over for the day - no line
            else:
                for (cls_i, _, y1_i), (cls_next, y0_next, _) in zip(layouts, layouts[1:]):
                    if cls_i["end"] < now < cls_next["start"]:
                        gap_y = (y1_i + y0_next) / 2
                        self._draw_time_indicator(x0, x1, gap_y)
                        break

    def _draw_time_indicator(self, x0, x1, y):
        """Draw the red current-time line + dot + 'now' pill at height y,
        spanning from x0 to x1. Used both inside a 'current' class card
        and, when now falls outside every class, before the first class
        or centered in a gap between two classes."""
        color = self.theme["TIME_LINE_COLOR"]
        self.canvas.create_line(
            x0 + 2, y, x1 - 2, y,
            fill=color, width=2
        )
        self.draw_rounded(
            x0 - 3, y - 5, x0 + 9, y + 5,
            r=6, fill=color
        )
        pill_text = "now"
        pill_w = 34
        self.draw_rounded(
            x1 - 16 - pill_w, y - 10,
            x1 - 16, y + 10,
            r=8, fill=color
        )
        self.canvas.create_text(
            x1 - 16 - pill_w / 2, y,
            text=pill_text, fill="#FFFFFF",
            font=("Helvetica", 8, "bold")
        )


if __name__ == "__main__":
    root = tk.Tk()
    app = CalendarShapeApp(root)
    root.mainloop()
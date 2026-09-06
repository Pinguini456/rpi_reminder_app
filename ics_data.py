"""
ICS fetching, caching, and parsing for the calendar app.

What this does:
- Downloads the two feeds (Canvas assessments, class timetable).
- Caches the raw .ics text to disk under ./data/, so the app still has
  something to show if a fetch fails (no internet, feed down, etc).
- Refreshes both feeds from the network once per hour, on a background
  thread, so the Tkinter UI thread is never blocked on a network call.
- Parses the cached .ics files into the plain structures the UI draws:
    store.get_next_assessment() -> dict or None
    store.get_todays_classes()  -> list of dicts, in start-time order

Testing note:
- TEST_TIME_OFFSET below lets you pretend "now" is shifted forward (or
  backward) by some amount, without touching your system clock. This is
  handy for testing the "today's classes" list and the current-time line
  against classes that are further out on your real timetable. Set it to
  `None` to go back to using the real current time.

Notes on the two feeds (checked against real samples on 2026-09-06):
- The Canvas assessments feed uses SUMMARY "Name [COURSE CODE]" and a
  DTSTART that is either a timed UTC datetime or an all-day DATE value
  (some entries even have a malformed duplicate "VALUE=DATE;VALUE=DATE"
  param — icalendar parses this fine).
- The class timetable feed has NO recurrence rules: every occurrence is
  already its own VEVENT with a concrete DTSTART/DTEND in UTC, so no
  RRULE expansion is needed. If a future export of this feed ever adds
  RRULEs, those events will currently be skipped rather than expanded —
  worth revisiting if that happens.

Dependencies: pip install requests icalendar tzdata --break-system-packages
("tzdata" is needed on Windows, since it has no system IANA tz database.)
"""

import os
import re
import threading
import time
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

import requests
from icalendar import Calendar

load_dotenv()

ASSESSMENTS_URL = os.getenv("ASSESSMENTS_URL")
CLASSES_URL = os.getenv("CLASSES_URL")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
ASSESSMENTS_CACHE = os.path.join(DATA_DIR, "assessments.ics")
CLASSES_CACHE = os.path.join(DATA_DIR, "classes.ics")

REFRESH_SECONDS = 60 * 60  # 1 hour
TZ = ZoneInfo("Pacific/Auckland")

_SUMMARY_COURSE_RE = re.compile(r"\[([^\[\]]+)\]\s*$")  # trailing "[COURSE CODE]"

# ---------------------------------------------------------------------
# TESTING OVERRIDE
# ---------------------------------------------------------------------
# Set this to a timedelta to make the whole app behave as if "now" is
# shifted by that much (e.g. testing what today's classes / current-time
# line look like 2 weeks out). Set it back to None for normal/live use.
TEST_TIME_OFFSET = timedelta(weeks=2)


def now():
    """Current time in NZ, shifted by TEST_TIME_OFFSET if it's set.

    Use this everywhere instead of datetime.now(TZ) so the whole app
    (class visibility, "today", the current-time line, assessment
    filtering, etc.) shifts consistently when testing."""
    real_now = datetime.now(TZ)
    return real_now + TEST_TIME_OFFSET if TEST_TIME_OFFSET else real_now


class ICSStore:
    """Owns the cached/parsed calendar data and keeps it fresh hourly."""

    def __init__(self, data_dir=None):
        self._data_dir = data_dir or DATA_DIR
        self._assessments_cache = os.path.join(self._data_dir, "assessments.ics")
        self._classes_cache = os.path.join(self._data_dir, "classes.ics")

        self._lock = threading.Lock()
        self._assessments = []  # sorted by due datetime, soonest first
        self._classes = []      # sorted by start datetime

        os.makedirs(self._data_dir, exist_ok=True)

        # Show whatever is cached on disk immediately, before the first
        # network fetch (which may take a few seconds) completes.
        self._load_from_cache()

        self._stop = False
        self._thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._thread.start()

    # ---- public API used by the UI ------------------------------------
    def get_next_assessment(self):
        """Soonest assessment due today or later, or None if there isn't one."""
        with self._lock:
            return dict(self._assessments[0]) if self._assessments else None

    def get_upcoming_assessments(self):
        """All assessments due today or later, soonest first."""
        with self._lock:
            return [dict(a) for a in self._assessments]

    def get_todays_classes(self):
        """Today's classes (local NZ date, per now()), sorted by start time."""
        today = now().date()
        with self._lock:
            todays = [c for c in self._classes if c["start"].date() == today]
        return sorted((dict(c) for c in todays), key=lambda c: c["start"])

    def stop(self):
        self._stop = True

    # ---- fetching / caching --------------------------------------------
    def _refresh_loop(self):
        while not self._stop:
            self._fetch_and_parse_all()
            # Sleep in 1s slices so stop() takes effect quickly instead of
            # blocking for up to an hour.
            for _ in range(REFRESH_SECONDS):
                if self._stop:
                    return
                time.sleep(1)

    def _fetch_and_parse_all(self):
        self._fetch_and_cache(ASSESSMENTS_URL, self._assessments_cache)
        self._fetch_and_cache(CLASSES_URL, self._classes_cache)
        self._load_from_cache()

    def _fetch_and_cache(self, url, cache_path):
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
        except Exception as exc:
            # Network hiccup: keep whatever is already cached on disk and
            # try again on the next hourly cycle.
            print(f"[ics] fetch failed for {url}: {exc}")
            return
        try:
            with open(cache_path, "wb") as f:
                f.write(resp.content)
        except OSError as exc:
            print(f"[ics] could not write cache {cache_path}: {exc}")

    def _load_from_cache(self):
        assessments = self._parse_assessments(self._assessments_cache)
        classes = self._parse_classes(self._classes_cache)
        with self._lock:
            if assessments is not None:
                self._assessments = assessments
            if classes is not None:
                self._classes = classes

    # ---- parsing ----------------------------------------------------------
    def _parse_assessments(self, path):
        cal = self._read_calendar(path)
        if cal is None:
            return None
        current = now()
        out = []
        for component in cal.walk("VEVENT"):
            summary = str(component.get("SUMMARY", "")).strip()
            name, course = self._split_summary(summary)
            dtstart = component.get("DTSTART")
            if dtstart is None:
                continue
            value = dtstart.dt
            if isinstance(value, datetime):
                due = value.astimezone(TZ) if value.tzinfo else value.replace(tzinfo=TZ)
                has_time = True
            elif isinstance(value, date):
                due = datetime.combine(value, datetime.min.time(), tzinfo=TZ)
                has_time = False
            else:
                continue
            out.append({
                "name": name,
                "course": course,
                "due": due,
                "has_time": has_time,
            })
        out.sort(key=lambda a: a["due"])
        today_start = datetime.combine(current.date(), datetime.min.time(), tzinfo=TZ)
        return [a for a in out if a["due"] >= today_start]

    def _parse_classes(self, path):
        cal = self._read_calendar(path)
        if cal is None:
            return None
        out = []
        for component in cal.walk("VEVENT"):
            summary = str(component.get("SUMMARY", "")).strip()
            dtstart = component.get("DTSTART")
            dtend = component.get("DTEND")
            if dtstart is None or dtend is None:
                continue
            start, end = dtstart.dt, dtend.dt
            if not isinstance(start, datetime) or not isinstance(end, datetime):
                continue  # skip all-day/malformed entries defensively
            start = start.astimezone(TZ) if start.tzinfo else start.replace(tzinfo=TZ)
            end = end.astimezone(TZ) if end.tzinfo else end.replace(tzinfo=TZ)
            location_raw = str(component.get("LOCATION", "")).strip()
            out.append({
                "course": self._course_from_class_summary(summary),
                "activity": self._activity_from_class_summary(summary),
                "location": self._shorten_location(location_raw),
                "start": start,
                "end": end,
            })
        out.sort(key=lambda c: c["start"])
        return out

    def _read_calendar(self, path):
        if not os.path.exists(path):
            return None
        try:
            with open(path, "rb") as f:
                return Calendar.from_ical(f.read())
        except Exception as exc:
            print(f"[ics] could not parse {path}: {exc}")
            return None

    @staticmethod
    def _split_summary(summary):
        """'Quiz 3 [ENGGEN 204]' -> ('Quiz 3', 'ENGGEN 204')"""
        m = _SUMMARY_COURSE_RE.search(summary)
        if m:
            return summary[:m.start()].strip(), m.group(1).strip()
        return summary, ""

    @staticmethod
    def _course_from_class_summary(summary):
        # 'ENGSCI 211/S1/C/LEC01/01 <10-12>' -> 'ENGSCI 211'
        return summary.split("/")[0].strip()

    @staticmethod
    def _activity_from_class_summary(summary):
        upper = summary.upper()
        if "/LEC" in upper:
            return "Lecture"
        if "/LAB" in upper:
            return "Lab"
        if "/TUT" in upper:
            return "Tutorial"
        return ""

    @staticmethod
    def _shorten_location(location):
        # '405-522 - 7 GRAFTON RD - MDLS Elec & Inst 5 (72)' -> '405-522'
        loc = location.split(" (")[0]
        parts = [p.strip() for p in loc.split(" - ")]
        return parts[0] if parts and parts[0] else loc
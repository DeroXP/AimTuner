"""Tracks: named sets of scenarios (what other aim trainers call playlists).

Core is the original nine - raw aim, no assistance, the fair way to compare two
controllers. Call of Duty adds that game's sensitivity scale, aim assist,
movement and time-to-kill on top, grouped by the mechanic each scenario trains.
Siege is Rainbow Six Siege's gunfight: its guns and recoil, one-bullet headshots,
soft walls and leaning, and - as in that game - no aim assist at all.
"""
from __future__ import annotations

from . import codscn, r6scn, scenarios


class Track:
    def __init__(self, id: str, name: str, blurb: str, groups):
        self.id, self.name, self.blurb = id, name, blurb
        self.groups = tuple((g, tuple(s)) for g, s in groups)
        self.scenarios = tuple(s for _, group in self.groups for s in group)

    def group_of(self, scn) -> tuple:
        for _, group in self.groups:
            if scn in group:
                return group
        return (scn,)


CORE = Track("core", "Core",
             "Nine scenarios of raw aim with no assistance. This is the track for comparing controllers.",
             (("", scenarios.ALL),))
COD = Track("cod", "Call of Duty",
            "27 scenarios on Call of Duty's sensitivity scale, with its aim assist, movement and a "
            "5-body-shot rifle. Three for each of nine mechanics.",
            codscn.GROUPS)
R6 = Track("r6", "Siege",
           "24 scenarios built on Rainbow Six Siege: its sensitivity scales and field of view, real guns with "
           "their own recoil, one-bullet headshots, soft walls, leaning. Three for each of eight skills. No "
           "aim assist, for anyone - Siege has none.",
           r6scn.GROUPS)

TRACKS = (CORE, COD, R6)
BY_TRACK = {t.id: t for t in TRACKS}
ALL = tuple(s for t in TRACKS for s in t.scenarios)
BY_ID = {s.id: s for s in ALL}

"""Decode Status.json, which the game rewrites several times a second.

Status.json has no matching journal event, so it has to be polled. The bit
layouts below follow Frontier's Player Journal documentation.
"""

from __future__ import annotations

# Status.Flags bit positions.
FLAGS = [
    "Docked",
    "Landed",
    "LandingGearDown",
    "ShieldsUp",
    "Supercruise",
    "FlightAssistOff",
    "HardpointsDeployed",
    "InWing",
    "LightsOn",
    "CargoScoopDeployed",
    "SilentRunning",
    "ScoopingFuel",
    "SrvHandbrake",
    "SrvTurretView",
    "SrvTurretRetracted",
    "SrvDriveAssist",
    "FsdMassLocked",
    "FsdCharging",
    "FsdCooldown",
    "LowFuel",
    "OverHeating",
    "HasLatLong",
    "IsInDanger",
    "BeingInterdicted",
    "InMainShip",
    "InFighter",
    "InSRV",
    "HudInAnalysisMode",
    "NightVision",
    "AltitudeFromAverageRadius",
    "FsdJump",
    "SrvHighBeam",
]

# Status.Flags2 bit positions (Odyssey).
FLAGS2 = [
    "OnFoot",
    "InTaxi",
    "InMulticrew",
    "OnFootInStation",
    "OnFootOnPlanet",
    "AimDownSight",
    "LowOxygen",
    "LowHealth",
    "Cold",
    "Hot",
    "VeryCold",
    "VeryHot",
    "GlideMode",
    "OnFootInHangar",
    "OnFootSocialSpace",
    "OnFootExterior",
    "BreathableAtmosphere",
    "TelepresenceMulticrew",
    "PhysicalMulticrew",
    "FsdHyperdriveCharging",
]

GUI_FOCUS = {
    0: "None",
    1: "InternalPanel",
    2: "ExternalPanel",
    3: "CommsPanel",
    4: "RolePanel",
    5: "StationServices",
    6: "GalaxyMap",
    7: "SystemMap",
    8: "Orrery",
    9: "FSS",
    10: "SAA",
    11: "Codex",
}


def decode_flags(value: int, names: list[str]) -> set[str]:
    """The names of the bits that are set in `value`, bit 0 first in `names`."""
    return {name for bit, name in enumerate(names) if value & (1 << bit)}


def active_flags(status: dict) -> set[str]:
    """All flag names that are on, from both Flags and Flags2."""
    return decode_flags(status.get("Flags", 0), FLAGS) | decode_flags(status.get("Flags2", 0), FLAGS2)


def flag_changes(old: dict | None, new: dict) -> tuple[set[str], set[str]]:
    """Return (turned_on, turned_off) between two Status.json snapshots."""
    before = active_flags(old) if old else set()
    after = active_flags(new)
    return after - before, before - after

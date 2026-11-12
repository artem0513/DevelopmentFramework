"""IANA reporting zones, with a verified Bucharest TZif for Windows."""
from datetime import timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class TimezoneDataUnavailable(ValueError):
    code = "TZ_DATA_UNAVAILABLE"

    def __init__(self, detail):
        super().__init__(self.code + ": " + str(detail))


# Preserve the stdlib file reader even when the optional host lookup is absent.
_from_file = ZoneInfo.from_file


def resolve_timezone(name, *, tzif_path=None):
    """Resolve UTC or IANA rules; never replace missing rules with a fixed offset.

    An explicit TZif is checked against the bundled zone's provenance too. This
    permits relocation without silently accepting a different or corrupt file.
    Other IANA zones can use the execution host's installed database.
    """
    if name == "UTC" and tzif_path is None:
        return timezone.utc
    if not isinstance(name, str) or not name or "\0" in name:
        raise TimezoneDataUnavailable("Invalid timezone name")
    root = Path(__file__).parent / "data" / "timezones"
    if name == "Europe/Bucharest" or tzif_path is not None:
        try:
            provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
            entry = provenance["zones"][name]
            path = Path(tzif_path) if tzif_path is not None else root / entry["path"]
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError("TZif checksum differs from verified provenance")
            return _from_file(BytesIO(data), key=name)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise TimezoneDataUnavailable(name + ": " + str(error)) from error
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError, OSError) as error:
        raise TimezoneDataUnavailable(name) from error

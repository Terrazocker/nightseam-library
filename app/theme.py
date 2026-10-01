"""Theme names shared by the desktop server and its static UI."""

THEME_WORKBENCH = "workbench"
# Kept only as an import-compatibility name for callers that normalize legacy
# saved settings. Glass is no longer a selectable or renderable theme.
THEME_GLASS = "dome"


def normalize_theme(value) -> str:
    """Return the one canonical theme name understood by the application.

    Every legacy value, including ``dome``, resolves to Workbench. Keeping this
    normalization at storage and import boundaries prevents an old setting or
    backup from selecting a retired stylesheet.
    """
    return THEME_WORKBENCH

"""Error types. Every error carries a stable machine-readable `code`."""


class PlayableKitError(Exception):
    code = "error"

    def __init__(self, message, **details):
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self):
        return {"code": self.code, "message": self.message, **({"details": self.details} if self.details else {})}


class UnsupportedBuildError(PlayableKitError):
    """The HTML does not look like a supported webpack/PixiJS playable build."""
    code = "unsupported_build"


class WorkspaceError(PlayableKitError):
    """Workspace missing, corrupt, or would be overwritten without --force."""
    code = "workspace_error"


class AssetError(PlayableKitError):
    """An asset file in the workspace is missing or unreadable."""
    code = "asset_error"


class ConversionError(PlayableKitError):
    """The build cannot be converted to the requested ad network."""
    code = "conversion_error"


class SmokeUnavailableError(PlayableKitError):
    """Runtime smoke test could not run (Node/Chrome missing or failed to start)."""
    code = "smoke_unavailable"

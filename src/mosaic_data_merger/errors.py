"""Domain-specific exceptions exposed by Mosaic Data Merger."""


class ConfigError(ValueError):
    """Raised when the job configuration cannot be safely executed."""


class RowError(ValueError):
    """Raised for a row that cannot be transformed or validated."""

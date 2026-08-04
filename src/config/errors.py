"""Configuration error type shared across the config package."""


class ConfigError(Exception):
    """Raised for invalid/incomplete configuration: missing env var, missing or
    malformed required field in a config YAML file.
    """

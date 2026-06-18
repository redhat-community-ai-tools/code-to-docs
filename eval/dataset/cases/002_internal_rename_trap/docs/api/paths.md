# Path Utilities

The `paths` module provides helpers for working with filesystem paths.

## normalize_path(path)

Returns an absolute, user-expanded version of `path`. Use this when you need a
canonical path for comparison or storage.

    from mytool.paths import normalize_path

    normalize_path("~/projects/../data")   # -> /home/you/data

This is the public, supported entry point. Its behavior is stable across releases.

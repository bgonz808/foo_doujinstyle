"""Source annotations consumed by pack_search_worker before embedding the zipapp."""


def standalone_only(function):
    """Keep this function in the standalone source tools."""
    return function


def worker_fallback(reason):
    """Record a retained recovery or compatibility path."""
    def annotate(function):
        return function

    return annotate


def native_worker_only(function):
    """Use native-only arm when generating the installed worker; preserve source reference."""
    return function


def runtime_deprecated(reason):
    """Mark a still-active installed Python path awaiting a tested native replacement.

    IMPORTANT: This is NOT soft deletion. pack_search_worker strips only this
    decorator and counts the function as active Python until migrated and
    marked standalone_only, or physically removed from the installed worker.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Runtime deprecation must name its replacement boundary")
    def annotate(function):
        return function
    return annotate

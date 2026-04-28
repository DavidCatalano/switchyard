"""Package smoke test — verifies the switchyard package is importable."""

import switchyard


def test_package_import() -> None:
    assert switchyard.__doc__ is not None

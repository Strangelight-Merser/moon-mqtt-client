"""Pinned scheduler calibration checks for the read-only native census."""


def require_worker_sample(baseline, opened, workers):
    assert workers in (1, 16), workers
    assert len(opened) == 1, opened
    assert opened[0] - baseline == workers + 1, (
        "worker calibration did not show the expected active coroutines",
        baseline, opened, workers,
    )


def require_pair(one, sixteen):
    assert one["binary_sha256"] == sixteen["binary_sha256"]
    assert one["baseline"] == sixteen["baseline"]
    require_worker_sample(one["baseline"], one["scope_open_counts"], 1)
    require_worker_sample(sixteen["baseline"], sixteen["scope_open_counts"], 16)
    assert (
        sixteen["scope_open_counts"][0] - sixteen["baseline"]
        - (one["scope_open_counts"][0] - one["baseline"])
    ) == 15

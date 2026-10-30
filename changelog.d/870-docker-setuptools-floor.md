### Fixed — the runtime image kept the base image's setuptools 79.0.1 (#870)

- The `Dockerfile` installs plain `aindy-runtime`, and setuptools's floor lives only in the
  `security` extra. So the image carried whatever setuptools `python:3.11-slim` ships (79.0.1 today), below the
  CVE-2026-59890 floor (83.0.0) and GHSA-xmj3-9gf7-h52w's (84.1.0). There was no runtime exposure:
  the runtime never imports setuptools, and both bugs are in install-time code. It is what a
  scanner of the image reports.
- The runtime stage now runs `pip install --upgrade "setuptools>=84.1.0"` after copying the
  builder's packages in. Installing it in the builder instead does not work. Copying `/install` over
  `/usr/local` leaves `setuptools-79.0.1.dist-info` beside the new one, and `pip list` still reports
  79.0.1 (checked in a throwaway two-stage build). `aindy-runtime[security]` was declined because
  that extra also installs `pip-audit` into the production image.
- This takes effect from the next image build. The floor is duplicated from `pyproject.toml`'s
  `security` extra; keep them equal.

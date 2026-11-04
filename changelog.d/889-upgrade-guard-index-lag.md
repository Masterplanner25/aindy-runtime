### Fixed — `Upgrade Path Guard` failed on a PR pushed just after a release (#889)

- The guard reads the newest version from PyPI's JSON API and installs it through the simple index,
  which can lag for minutes after a publish. In that window it failed with `No matching distribution
  found` before any schema step, so a red guard said nothing about the upgrade path.
- It now retries the install up to 10 times, 30 s apart, and still fails if the version never resolves.

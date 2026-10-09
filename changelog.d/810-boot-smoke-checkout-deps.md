### Changed — Boot Smoke boots the checkout with the checkout's declared dependencies (#810, DEC-098)

- Boot Smoke runs the checkout's source (`PYTHONPATH: .`) over the published wheel, so the wheel
  supplied only its dependency set. A PR whose code needed a dependency the release lacked failed
  this required check, and only a release could clear it. #810 hit it first: replacing
  python-jose with PyJWT failed with `No module named 'jwt'`.
- A step now installs the checkout's `[project].dependencies` on top of the wheel and logs the
  `pip freeze` diff. At release the checkout is the tag, so nothing moves and the check proves
  what it proved before.

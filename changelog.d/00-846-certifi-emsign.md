### Fixed — `certifi` pinned to a bundle with compromised CA roots (PYSEC-2026-4320, FR-54, #846)

- `certifi==2026.7.22` bundled "emSign ECC Root CA - G3", whose eMudhra intermediates have
  compromised (revoked) keys, so every outbound HTTPS call trusted certificates anyone holding one
  could issue. Now `certifi>=2026.10.19`.
- **A lower bound, not a pin (DEC-102):** an exact pin meant a downstream constraint could not take
  a fixed CA bundle until the runtime released. Future certifi releases install without one.
- **Operators: rebuild your image.** The advisory asks for a rebuild of any image that vendors
  certifi; upgrading the package in a running container is not enough if the image is rebuilt from
  an older layer.

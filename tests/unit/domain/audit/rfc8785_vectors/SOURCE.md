# Source

Vendored verbatim (byte-for-byte, no edits) from the RFC 8785 reference
implementation's own test suite:

https://github.com/cyberphone/json-canonicalization/tree/master/testdata

Copyright 2018 Anders Rundgren, licensed Apache License 2.0
(https://github.com/cyberphone/json-canonicalization/blob/master/LICENSE).

Six pairs: arrays, french, structures, unicode, values, weird — each an
`*.input.json` (pre-canonicalization) and `*.output.json` (the canonical
form `jcs()` must produce). Used entirely offline by
`tests/unit/domain/audit/test_canonical.py`; nothing in this repository
fetches them at test or runtime.

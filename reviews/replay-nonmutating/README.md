# Non-mutating replay verification

The previous replay verifier used the committed replay directory as scratch output. Volatile wall-clock fields therefore changed tracked evidence during `make check`. The repaired verifier uses a disposable directory, validates the committed replay bundle independently, compares live, committed-replay and fresh-replay semantics, and deletes the disposable run.

A regression test hashes all five canonical replay files before and after execution. Full qualification passed with 173 tests, and the Git status hash remained identical across `make check`.

# Adversarial round 5 — installed human demo and runtime recovery

This round treated first contact as an installed-product problem rather than a source-repository documentation problem. It began from private `main` after the downloaded-release verifier checkpoint and attempted the advertised demo from a freshly installed wheel, outside the source checkout, without activating the virtual environment.

The audit found two release blockers: no installed `driftproof demo` command existed, and directly invoking the wheel could not discover `dbt` installed beside its Python unless the environment had first been activated. The repair embeds transparent safe and unsafe fixtures in the package, publishes a typed and independently verifiable evidence tree atomically, and discovers `dbt` either on `PATH` or beside the active Python.

The successful and missing-dbt wheel paths were both executed. Existing output was preserved, missing prerequisites returned one fail-closed JSON object, and the legacy repository wrapper now delegates to the installed command rather than implementing a divergent demo.

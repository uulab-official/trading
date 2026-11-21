# Rust maintenance implementation plan

1. Verify the clean published baseline, collect exact Rust compiler diagnostics,
   and recheck credential presence without exposing values or making requests.
2. Independently inspect binary/library structure and module-level warnings.
   Install the pinned compiler in task-local directories for actual validation.
3. In parallel, switch the binary to the public library and add parser-only
   compatibility tests; clean unused private imports/items without changing
   public APIs or connecting unfinished adapters.
4. Enable package-scoped compiler warnings as errors. Extend the Docker CLI
   contract with the migration help path and verify failure before the workflow
   change, then success afterwards.
5. Format changed Rust code, run locked tests/release build, a negative lint-gate
   probe, Python verification, and the synthetic paper lab as appropriate.
6. Obtain independent whole-change review, resolve concrete findings, publish
   reviewed byte-identical files with an expected-head lease, and verify the
   final commit's complete required CI before reporting completion.

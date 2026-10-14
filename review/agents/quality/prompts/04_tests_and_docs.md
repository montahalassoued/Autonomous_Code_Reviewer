Tests and documentation:
- New behavior or a fixed bug with no test. Only claim a test is missing when you can see the tests in the diff or the context; if no tests are shown at all, set "confidence" to "low"
- Tests that assert almost nothing (only "is not None", only that no exception was raised)
- Flaky tests: the real network, sleep or timing, a dependency on test order or shared state
- Changed behavior whose existing tests (visible in the context) were not updated
- A new public function, class or endpoint with no documentation, or documentation that no longer matches the code

# 6. Integration Tests

Integration testing is often optional, but is important for us to test interaction with other systems (and in our terminology this is also lumped with end-to-end tests). These tests are found in the `tests/integration` directory.

Unlike unit tests, these tests can use real secrets. To test locally, these secrets should be configured as usual and it will make use of your `.env`.

These are tested regularly by the PyRIT team but not necessarily run every pull request. Here are some general guidelines.

- Unit tests should test all scenarios and more is often better. Integration tests should be scoped and be careful to not run too long.
- Integration tests can sometimes test end-to-end. But almost always, these should target one scenario.

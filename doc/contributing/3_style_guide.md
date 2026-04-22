# 3. Style Guide

Currently the code in PyRIT should try to have a consistent style. A consistent coding style aids in efficient development of capabilities.


We use the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html).
For [docstrings](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings),
there are rules for modules, classes, and functions.

While we can't expect all documentation to be perfect from day 1, we'll improve any docstring
that we touch in a pull request to be compliant with this style guide.

The PyRIT codebase uses whitespace for organization / visual clues so developers can quickly scan and understand relation based on whitespace in addition to other features.

Deviations from any particular rule can occur depending on context and need.

- Strict rules should be included in the pre-commit hooks. This is currently run as part of the build pipelines. If we want to enforce style, it's best to do here.
- Use explicit types and return values when possible
- Comments are highly encouraged. We use docstrings under the function. The docstring format should align with Google guidelines .
- Long line lengths are discouraged (>= ~120)
- Large files are discouraged. If possible put one class per file or at least put only related classes in the same file. Large files should be broken up.
- Fight indentation and scoping. Lots of scopes makes code more difficult to read and properly write. Use the following to fight indentation: early function exits, logic inversion/continue, exceptions (as appropriate), creating smaller functions, goto's, etc.
- Test code should adhere to these coding guidelines as well.
- One empty line between related (in-class things)
- Two empty lines between unrelated things (classes, global functions, etc)
- One parameter per line
- Spaces not tabs. Tab value must be 4 spaces
- Test names should be test_foo test_bar, test_baz, etc.
- In the case of type name conflicts, the desired type should be used in its fully-qualified (or disambiguating relatively-qualified) form: e.g. "pyrit.shared.foo"
- Naming should follow typical Python naming. e.g. some_descriptive_name
- PyRIT imports go last (after a newline)
- Imports should go in alphabetical order

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

class TestBenchmark:
    """
    Test benchmark scenario.

    Main failure modes specific to benchmark:
    - Bad formatting of AttackTechniqueSpec.
    - Trying to modify a mutable AttackTechniqueSpec object rather than
      recreating it.
    - Incorrect number of tuples (dataset x technique x adversarial_model)
    - Ingesting non-adversarial models (TBD; one could imagine deliberately
      passing an aligned model and k-many unaligned ones to benchmark them.)
    - Custom methods, including get_atomic_attacks_async.
    - Optional: AML endpoint parsing. May be out of scope since the contract 
      is assumed to hold but we can add tests for various different types of PromptTargets
      and see if benchmarking / comparison / scoring fails since that's unique to this
      class.
    """
    pass

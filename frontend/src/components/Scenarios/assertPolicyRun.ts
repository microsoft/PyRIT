const ASSERT_POLICY_REGISTRY_NAME = 'foundry.assert_policy'
const ASSERT_POLICY_CLASS_NAME = 'AssertPolicy'

/**
 * Whether a run summary may describe a foundry.assert_policy scan.
 *
 * A stored registry name decides. Older runs without one are matched by class name only tentatively:
 * the metrics route confirms the scenario's module and answers with a not-an-ASSERT-run problem type otherwise.
 */
export function isAssertPolicyRun(run: { scenario_registry_name?: string | null; scenario_name: string }): boolean {
  if (run.scenario_registry_name) return run.scenario_registry_name === ASSERT_POLICY_REGISTRY_NAME
  return run.scenario_name === ASSERT_POLICY_CLASS_NAME
}

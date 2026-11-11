const ASSERT_POLICY_REGISTRY_NAME = 'foundry.assert_policy'
const ASSERT_POLICY_CLASS_NAME = 'AssertPolicy'

/** Whether a run summary describes a foundry.assert_policy scan. */
export function isAssertPolicyRun(run: { scenario_registry_name?: string | null; scenario_name: string }): boolean {
  return run.scenario_registry_name === ASSERT_POLICY_REGISTRY_NAME || run.scenario_name === ASSERT_POLICY_CLASS_NAME
}

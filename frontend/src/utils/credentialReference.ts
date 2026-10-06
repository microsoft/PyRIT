// Mirrors CredentialReference.env_var in pyrit/models/catalog/instance_recipe.py.
const ENVIRONMENT_VARIABLE_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/
const ENVIRONMENT_VARIABLE_RULE = 'Use letters, digits, and underscores, not starting with a digit.'

/** Returns the rule an environment variable name breaks, or null when the name is blank or valid. */
export function environmentVariableError(name: string): string | null {
  return name && !ENVIRONMENT_VARIABLE_PATTERN.test(name) ? ENVIRONMENT_VARIABLE_RULE : null
}

import type {
  Reporter,
  TestCase,
  TestResult,
} from "@playwright/test/reporter";

class NoSkippedTestsReporter implements Reporter {
  private readonly skippedTests: string[] = [];

  onTestEnd(test: TestCase, result: TestResult): void {
    if (
      process.env.FAIL_ON_SKIPPED_TESTS === "true" &&
      result.status === "skipped"
    ) {
      this.skippedTests.push(test.titlePath().filter(Boolean).join(" > "));
    }
  }

  onEnd(): { status: "failed" } | void {
    if (this.skippedTests.length === 0) {
      return;
    }

    console.error(
      `Unexpected skipped tests:\n${this.skippedTests.map((title) => `- ${title}`).join("\n")}`,
    );
    return { status: "failed" };
  }
}

export default NoSkippedTestsReporter;

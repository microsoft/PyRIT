import { expect, test, type Page } from "./_fixtures";
import { mockVersion } from "./_compatibility";
import { READY_RUNTIME } from "./_runtime";

const LONG_HARM_CATEGORY = "cybersecurity_credentials_and_privileged_information_disclosure";

const DATASET = {
  name: "harmbench",
  selection_key: "dataset:named:harmbench",
  loaded: true,
  provider_available: true,
  logical_examples: 12,
  seed_pieces: 12,
  objectives: 1,
  modalities: ["text"],
  harm_categories: [LONG_HARM_CATEGORY],
  has_unlabeled_harm_categories: false,
};

const NEIGHBOR_DATASET = {
  ...DATASET,
  name: "neighbor",
  selection_key: "dataset:named:neighbor",
};

async function mockDatasetCatalog(page: Page): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const apiPath = new URL(route.request().url()).pathname.replace(/^\/api/, "");
    const body = (payload: unknown) => ({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });

    if (apiPath === "/datasets") {
      await route.fulfill(body({ items: [DATASET, NEIGHBOR_DATASET] }));
      return;
    }
    if (apiPath === "/runtime") {
      await route.fulfill(body(READY_RUNTIME));
      return;
    }
    if (apiPath === "/version") {
      await route.fulfill(body(mockVersion()));
      return;
    }
    if (apiPath === "/auth/config") {
      await route.fulfill(body({ clientId: "", tenantId: "", allowedGroupIds: "" }));
      return;
    }
    if (apiPath === "/auth/access") {
      await route.fulfill(body({ isAdmin: false }));
      return;
    }
    if (apiPath === "/health") {
      await route.fulfill(body({ status: "healthy" }));
      return;
    }
    if (apiPath === "/targets" || apiPath === "/converters" || apiPath === "/converters/types") {
      await route.fulfill(body({ items: [] }));
      return;
    }
    if (apiPath === "/targets/types") {
      await route.fulfill(body({ items: [] }));
      return;
    }
    if (apiPath === "/labels") {
      await route.fulfill(body({ source: "attacks", labels: {} }));
      return;
    }

    throw new Error(`Unhandled dataset catalog request: ${route.request().method()} ${apiPath}`);
  });
}

async function expectLongLabelStaysInsideCard(page: Page): Promise<void> {
  const card = page.getByRole("article", { name: "harmbench" });
  await expect(card).toBeVisible();
  await expect(card.getByText(LONG_HARM_CATEGORY)).toBeVisible();

  const metrics = await card.evaluate((article, label) => {
    const badge = Array.from(article.querySelectorAll("*")).find((element) => element.textContent === label);
    const textHost = badge ?? article;
    const range = document.createRange();
    range.selectNodeContents(textHost);
    const text = range.getBoundingClientRect();
    const cardBox = article.getBoundingClientRect();
    const badgeBox = textHost.getBoundingClientRect();
    return {
      cardWidth: cardBox.width,
      cardRight: cardBox.right,
      cardLeft: cardBox.left,
      cardTop: cardBox.top,
      cardBottom: cardBox.bottom,
      textLeft: text.left,
      textRight: text.right,
      textTop: text.top,
      textBottom: text.bottom,
      badgeHeight: badgeBox.height,
      scrollWidth: textHost.scrollWidth,
      clientWidth: textHost.clientWidth,
    };
  }, LONG_HARM_CATEGORY);

  expect(metrics.cardWidth).toBeLessThanOrEqual(360);
  expect(metrics.textLeft).toBeGreaterThanOrEqual(metrics.cardLeft - 1);
  expect(metrics.textRight).toBeLessThanOrEqual(metrics.cardRight + 1);
  expect(metrics.textTop).toBeGreaterThanOrEqual(metrics.cardTop - 1);
  expect(metrics.textBottom).toBeLessThanOrEqual(metrics.cardBottom + 1);
  expect(metrics.badgeHeight).toBeGreaterThan(20);
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1);
}

test.describe("dataset catalog layout", () => {
  test.beforeEach(async ({ page }) => {
    await mockDatasetCatalog(page);
  });

  test("wraps a long harm category inside a multi-column desktop catalog", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1050 });
    await page.goto("/datasets");
    await expectLongLabelStaysInsideCard(page);

    const cards = page.getByRole("article");
    await expect(cards).toHaveCount(2);
    const [first, second] = await cards.evaluateAll((articles: Element[]) =>
      articles.map((article: Element) => {
        const { left, right, top } = article.getBoundingClientRect();
        return { left, right, top };
      }),
    );
    expect(first.top).toBe(second.top);
    expect(second.left).toBeGreaterThan(first.right);
  });

  test("wraps a long harm category inside the card on a 390px mobile viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/datasets");
    await expectLongLabelStaysInsideCard(page);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });
});

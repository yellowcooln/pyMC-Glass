import { expect, test } from "@playwright/test";

test("isolated unauthenticated navigation redirects to the Glass sign-in page", async ({ page, baseURL }) => {
  if (!baseURL) throw new Error("An explicit isolated baseURL is required");
  const origin = new URL(baseURL).origin;
  const pageErrors: string[] = [];
  const blockedRequests: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    // Do not follow redirects/assets to any other host or permit API writes.
    if (url.origin !== origin || !["GET", "HEAD"].includes(request.method())) {
      blockedRequests.push(`${request.method()} ${url.origin}${url.pathname}`);
      await route.abort();
      return;
    }
    await route.continue();
  });
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Glass", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  expect(pageErrors).toEqual([]);
  expect(blockedRequests).toEqual([]);
});

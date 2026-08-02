import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("document favicon", () => {
  it("uses the Aegis Runtime favicon asset", () => {
    const indexHtml = readFileSync(resolve(import.meta.dirname, "../index.html"), "utf8");

    expect(indexHtml).toContain('href="/aegis-runtime-favicon.png?v=2"');
  });
});

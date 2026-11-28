import { describe, expect, it } from "vitest";
import { money } from "./format";

describe("money", () => {
  it("shows cents, and more digits for tiny amounts", () => {
    expect(money(null)).toBe("—");
    expect(money(0)).toBe("$0.00");
    expect(money(0.00042)).toBe("$0.0004");
    expect(money(12.5)).toBe("$12.50");
    expect(money(1234.567)).toBe("$1,234.57");
  });
});

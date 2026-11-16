import { describe, it, expect, vi } from "vitest";
import { Api, ApiError } from "./api";
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
describe("typed API boundary", () => {
  it("sends account credentials without tenant/role and disables storage", async () => {
    const fetch = vi.fn().mockResolvedValue(json({}));
    vi.stubGlobal("fetch", fetch);
    await new Api().login("acme-demo", "synthetic");
    const [path, init] = fetch.mock.calls[0];
    expect(path).toBe("/api/v1/demo/session");
    expect(JSON.parse(init.body)).toEqual({
      username: "acme-demo",
      password: "synthetic",
    });
    expect(init.cache).toBe("no-store");
    expect(init.credentials).toBe("omit");
    expect(init.redirect).toBe("error");
  });
  it("uses Bearer authentication and existing query limits/defaults", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(json({ answer: "ok", citations: [] }));
    vi.stubGlobal("fetch", fetch);
    await new Api("memory-token").query("Remote allowance?");
    const [, init] = fetch.mock.calls[0];
    expect(init.headers.get("Authorization")).toBe("Bearer memory-token");
    expect(JSON.parse(init.body)).toEqual({
      query: "Remote allowance?",
      use_cache: true,
    });
  });
  it("uploads multipart with UUID key and no manual content-type", async () => {
    const fetch = vi.fn().mockResolvedValue(json({}));
    vi.stubGlobal("fetch", fetch);
    const file = new File(["PDF"], "policy.pdf");
    await new Api("token").upload(file, "generated-key");
    const [, init] = fetch.mock.calls[0];
    expect(init.headers.get("Idempotency-Key")).toBe("generated-key");
    expect(init.headers.has("Content-Type")).toBe(false);
    expect(init.body.get("file").name).toBe("policy.pdf");
  });
  it("sanitizes provider errors and never echoes server payloads", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json({ detail: "PRIVATE path / secret" }, 500)),
    );
    await expect(new Api().documents()).rejects.toThrow(
      "The request could not be completed.",
    );
  });
  it("classifies provider-disabled and ambiguous publishing without retries", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json({ detail: "Demo provider is disabled" }, 503))
      .mockResolvedValueOnce(
        json({ code: "submission_unknown", job_id: "job-1" }, 503),
      );
    vi.stubGlobal("fetch", fetch);
    await expect(new Api().query("q")).rejects.toMatchObject({
      providerDisabled: true,
    });
    await expect(
      new Api().upload(new File(["x"], "x.pdf"), "k"),
    ).rejects.toMatchObject({ code: "submission_unknown", jobId: "job-1" });
    expect(fetch).toHaveBeenCalledTimes(2);
  });
  it("rejects non-PDF downloads", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({})));
    await expect(new Api().pdf("doc")).rejects.toThrow("Invalid PDF response");
  });
  it("exposes safe limit feedback", () => {
    expect(new ApiError(429).message).toContain("Demo limit reached");
  });
});

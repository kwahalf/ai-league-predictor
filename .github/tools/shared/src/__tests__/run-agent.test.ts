import { describe, expect, it, vi } from "vitest";

vi.mock("@github/copilot-sdk", () => ({
  CopilotClient: vi.fn().mockImplementation(() => ({})),
  approveAll: vi.fn(() => ({ kind: "approve-once" })),
}));

vi.mock("../logging.js", () => ({
  info: vi.fn(),
  error: vi.fn(),
  writeSummary: vi.fn(),
  attachLogger: vi.fn(),
}));

import { CopilotClient } from "@github/copilot-sdk";
import {
  buildTemplateVars,
  buildRunPrompt,
  buildSessionConfig,
  createCopilotClient,
  resolveTimeoutMs,
} from "../run-agent.js";
import { approveAll } from "../permissions.js";

describe("run-agent helpers", () => {
  it("builds run prompt with full system prompt body", () => {
    const systemPrompt = "Line one\nLine two";
    const prompt = buildRunPrompt(
      "project-manager",
      { owner: "<ORG>", repo: "<REPO_NAME>" },
      systemPrompt,
      "2026-01-01T00:00:00.000Z"
    );

    expect(prompt).toContain("You are the **project-manager** agent");
    expect(prompt).toContain("Current time: 2026-01-01T00:00:00.000Z");
    expect(prompt).toContain(systemPrompt);
    expect(prompt).toContain("Please perform your full standard run now.");
  });

  it("builds createSession config with systemMessage content", () => {
    const systemPrompt = "Agent instructions go here";
    const config = buildSessionConfig("gpt-5.5", systemPrompt);

    expect(config).toEqual({
      model: "gpt-5.5",
      onPermissionRequest: approveAll,
      systemMessage: { content: systemPrompt },
    });
    expect((config as { systemPrompt?: unknown }).systemPrompt).toBeUndefined();
  });

  it("sets session working directory when provided", () => {
    const config = buildSessionConfig("gpt-5.5", "prompt", "/repo/root");
    expect(config.workingDirectory).toBe("/repo/root");
  });

  it("constructs Copilot client with explicit github token", () => {
    createCopilotClient("token-123");
    expect(CopilotClient).toHaveBeenCalledWith({ gitHubToken: "token-123" });
  });
});

describe("resolveTimeoutMs", () => {
  it("prefers the agent frontmatter timeout_minutes", () => {
    const ms = resolveTimeoutMs(
      { timeout_minutes: 15 },
      { factory: { agent_timeout_minutes: 10 } }
    );
    expect(ms).toBe(15 * 60 * 1000);
  });

  it("falls back to the factory config default", () => {
    const ms = resolveTimeoutMs({}, { factory: { agent_timeout_minutes: 10 } });
    expect(ms).toBe(10 * 60 * 1000);
  });

  it("falls back to the built-in default when neither is set", () => {
    const ms = resolveTimeoutMs({}, { factory: {} });
    expect(ms).toBe(10 * 60 * 1000);
  });
});

describe("buildTemplateVars", () => {
  it("includes default_branch for agent prompt interpolation", () => {
    const vars = buildTemplateVars(
      {
        owner: "<ORG>",
        repo: "<REPO_NAME>",
        runUrl: "https://github.com/<ORG>/<REPO_NAME>/actions/runs/1",
      },
      {
        repository: { default_branch: "main" },
        factory: { max_open_copilot_prs: 3 },
      }
    );

    expect(vars).toMatchObject({
      owner: "<ORG>",
      repo: "<REPO_NAME>",
      run_url: "https://github.com/<ORG>/<REPO_NAME>/actions/runs/1",
      max_open_copilot_prs: 3,
      default_branch: "main",
    });
  });
});

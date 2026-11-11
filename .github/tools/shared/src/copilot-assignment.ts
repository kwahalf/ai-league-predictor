import { execSync } from "node:child_process";
import { info, error } from "./logging.js";

export interface AssignCopilotOptions {
  owner: string;
  repo: string;
  issueNumber: number;
}

export function assignCopilotToIssue({ owner, repo, issueNumber }: AssignCopilotOptions): void {
  try {
    execSync(
      `gh issue edit ${issueNumber} --repo ${owner}/${repo} --add-assignee "copilot-swe-agent[bot]"`,
      { stdio: "pipe" }
    );
    info("Assigned Copilot to issue", { issue: issueNumber });
  } catch (err) {
    error("Failed to assign Copilot", { issue: issueNumber, err: String(err) });
    throw err;
  }
}

export function addLabel(owner: string, repo: string, issueNumber: number, label: string): void {
  execSync(`gh issue edit ${issueNumber} --repo ${owner}/${repo} --add-label "${label}"`, {
    stdio: "pipe",
  });
}

export function removeLabel(owner: string, repo: string, issueNumber: number, label: string): void {
  execSync(`gh issue edit ${issueNumber} --repo ${owner}/${repo} --remove-label "${label}"`, {
    stdio: "pipe",
  });
}

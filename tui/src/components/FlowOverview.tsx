import React from "react";
import { Box, Text } from "ink";

/**
 * Static per-flow overview blurbs. Hardcoded by design (see issue
 * prefect-orchestration-5vh) — when we grow beyond two flows, factor
 * into a registry / entry-point group then.
 */
export const FLOW_OVERVIEWS: Record<string, string> = {
  software_dev_full:
    "triage → plan ⟲ → build → lint+test → regression → review ⟲ → deploy-smoke → verification ⟲ → ralph ⟲ → docs → learn",
  epic: "epic fan-out: DAG-ordered parallel children",
};

/** Pure helper — testable without rendering. Returns null for unknown flows. */
export function getFlowOverview(flowName: string | undefined): string | null {
  if (!flowName) return null;
  return FLOW_OVERVIEWS[flowName] ?? null;
}

interface Props {
  flowName: string | undefined;
}

export function FlowOverview({ flowName }: Props): React.ReactElement | null {
  if (!flowName) return null;
  const overview = getFlowOverview(flowName);
  return (
    <Box paddingX={1}>
      <Text bold color="white">
        {flowName}
      </Text>
      {overview ? (
        <Text color="gray" wrap="truncate-end">
          {" · "}
          {overview}
        </Text>
      ) : null}
    </Box>
  );
}

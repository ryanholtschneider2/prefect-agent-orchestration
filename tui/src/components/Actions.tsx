import React from "react";
import { Box, Text } from "ink";

interface Props {
  issueId: string | null;
  flowRunId?: string;
  prefectUrl?: string;
}

export function Actions({ issueId, flowRunId, prefectUrl }: Props): React.ReactElement {
  const base = prefectUrl ?? "http://127.0.0.1:4200";
  const prefectUiUrl = flowRunId
    ? `${base}/flow-runs/flow-run/${flowRunId}`
    : null;

  return (
    <Box flexDirection="column" paddingX={1} paddingY={1}>
      <Text bold color="white">
        ACTIONS{" "}
        {issueId ? (
          <Text color="cyan">{issueId}</Text>
        ) : (
          <Text color="gray">(none selected)</Text>
        )}
      </Text>
      <Box flexDirection="column" marginTop={1}>
        <Text color="gray">[c]  Cancel this run (confirm prompt)</Text>
        <Text color="gray">[r]  Retry this run (confirm prompt)</Text>
        <Text color="gray">[d]  Dispatch new run (form)</Text>
        {prefectUiUrl ? (
          <Text color="gray">[o]  Open in Prefect UI — {prefectUiUrl}</Text>
        ) : (
          <Text color="gray">[o]  Open in Prefect UI (no run selected)</Text>
        )}
        <Text color="gray">[s]  Show artifacts dir</Text>
      </Box>
    </Box>
  );
}

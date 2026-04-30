import React from "react";
import { Box, Text } from "ink";

import { BdShow } from "./BdShow.js";
import { FlowOverview } from "./FlowOverview.js";
import { RoleTimeline } from "./RoleTimeline.js";
import { TmuxTail } from "./TmuxTail.js";
import { Actions } from "./Actions.js";
import type { IssueRow } from "../state/store.js";
import type { TabName } from "../state/store.js";
import type { BdIssue } from "../data/beads.js";

interface Props {
  selectedTab: TabName;
  selected: IssueRow | undefined;
  allIssues: IssueRow[];
  paneText: string;
  paneSession: string | null;
  tailHeight: number;
  bdShowIssue: BdIssue | null;
  issueId: string | null;
  bdShowError: string | null;
  bdScrollOffset: number;
  prefectUrl?: string;
  mobile: boolean;
}

const TABS: TabName[] = ["LIVE", "TRACE", "BD", "ACTIONS"];

export function DetailTabs(props: Props): React.ReactElement {
  return (
    <Box flexDirection="column" flexGrow={1} borderStyle="round" borderColor="gray">
      {/* Tab bar */}
      <Box flexDirection="row" paddingX={1}>
        {TABS.map((t) => (
          <Box key={t} marginRight={1}>
            <Text bold={t === props.selectedTab} color={t === props.selectedTab ? "cyan" : "gray"}>
              [{t}]
            </Text>
          </Box>
        ))}
        <Text color="gray"> [t] cycle  [1-4] jump</Text>
      </Box>

      {/* Content */}
      <Box flexDirection="column" flexGrow={1}>
        {props.selectedTab === "LIVE" && (
          <>
            <FlowOverview flowName={props.selected?.flowName} />
            <RoleTimeline issue={props.selected} allIssues={props.allIssues} />
            <Box borderStyle="single" borderColor="gray" flexDirection="column">
              <TmuxTail text={props.paneText} session={props.paneSession} height={props.tailHeight} />
            </Box>
          </>
        )}
        {props.selectedTab === "TRACE" && (
          <Box paddingX={1} paddingY={1}>
            <Text color="gray">(coming soon — see prefect-orchestration-qhg)</Text>
          </Box>
        )}
        {props.selectedTab === "BD" && (
          <Box borderStyle="single" borderColor="gray" flexDirection="column">
            <BdShow
              issue={props.bdShowIssue}
              issueId={props.issueId}
              error={props.bdShowError}
              height={props.tailHeight}
              scrollOffset={props.bdScrollOffset}
            />
          </Box>
        )}
        {props.selectedTab === "ACTIONS" && (
          <Actions
            issueId={props.issueId}
            flowRunId={props.selected?.flowRunId}
            prefectUrl={props.prefectUrl}
          />
        )}
      </Box>
    </Box>
  );
}

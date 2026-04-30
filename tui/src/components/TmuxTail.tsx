import React from "react";
import { Box, Text } from "ink";

interface Props {
  text: string;
  session: string | null;
  height: number;
}

/**
 * Renders the tail of a tmux pane. We trim to the most recent `height` rows
 * so Ink doesn't have to lay out 200 lines every tick.
 */
export function TmuxTail({ text, session, height }: Props): React.ReactElement {
  const lines = text.split(/\r?\n/);
  // Drop trailing blank lines tmux pads with.
  while (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  const visible = lines.slice(-height);

  return (
    <Box flexDirection="column" paddingX={1}>
      <Text bold color="white">
        TMUX TAIL{" "}
        <Text color="gray">{session ? `— ${session}` : "— (no session)"}</Text>
      </Text>
      <Box flexDirection="column" marginTop={1}>
        {visible.length === 0 ? (
          <Text color="gray">(empty)</Text>
        ) : (
          visible.map((line, i) => (
            <Text key={i} color="white" wrap="truncate-end">
              {line || " "}
            </Text>
          ))
        )}
      </Box>
    </Box>
  );
}

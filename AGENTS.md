# Progress reporting

- The user requests ongoing progress reports from every active agent at least once every two minutes.
- Report briefly in Korean: completed work or findings, the next step, and any blocker.
- Subagents should send these reports to the primary agent. The primary agent should relay meaningful updates to the user with the agent's task identified.
- If a tool call prevents a timely update, report as soon as the call returns. Do not claim a precise timer guarantee while a tool is blocking.
- Pass this reporting requirement to any newly delegated subagents. Continue the authorized task while reporting.
